# -*- coding: utf-8 -*-
"""
Codex Auto-Resume Pure System Tray Application
Нативный системный трей Windows, настройки на PyQt6, мультиязычность (RU/EN),
фильтрация ошибок и проектов, авто-включение при старте Кодекса.
"""

import sys
import os
import time
import json
import sqlite3
import ctypes
from ctypes import wintypes
import logging
from logging.handlers import RotatingFileHandler
import threading
import subprocess
import re
from datetime import datetime, timedelta
from typing import NamedTuple

def _early_attach_default_desktop():
    try:
        user32 = ctypes.windll.user32
        h_desk = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW(h_desk, 2, buf, 512, ctypes.byref(needed))
        if buf.value.lower() != "default":
            h_def = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if h_def:
                user32.SetThreadDesktop(h_def)
    except Exception:
        pass

_early_attach_default_desktop()

import win32gui
import win32con
import win32process
import psutil

from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QWidget
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QRect, QPoint
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QBrush, QPen, QPolygon

# Импорт менеджера настроек и окна настроек
from settings_gui import settings_mgr, SettingsWindow, get_all_codex_projects, register_codex_home, read_chat_catalog

# -------------------------------------------------------------
# Конфигурация путей
# -------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
USER_PROFILE = os.environ.get("USERPROFILE", r"C:\Users\Artur")
CODEX_DIR = os.path.join(USER_PROFILE, ".codex")
os.makedirs(CODEX_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(CODEX_DIR, "guardian_settings.json")
LOG_FILE = os.path.join(CODEX_DIR, "guardian.log")
TOGGLE_TRIGGER = os.path.join(CODEX_DIR, "toggle.trigger")


def history_db(home=None):
    return os.path.join(home or CODEX_DIR, "thread_history_1.sqlite")

def goals_db(home=None):
    return os.path.join(home or CODEX_DIR, "goals_1.sqlite")

def session_index_file(home=None):
    return os.path.join(home or CODEX_DIR, "session_index.jsonl")
AUTOSTART_VBS = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup\CodexAutoResumeWatchdog.vbs"
)

# -------------------------------------------------------------
# Настройка логирования
# -------------------------------------------------------------
logger = logging.getLogger("CodexTrayApp")
logger.setLevel(logging.INFO)
if not logger.handlers:
    rfh = RotatingFileHandler(LOG_FILE, maxBytes=2*1024*1024, backupCount=3, encoding="utf-8")
    rfh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(rfh)

_desktop_attached = False

def attach_desktop():
    """Безопасно переключает поток на рабочий стол Default (интерактивный рабочий стол Windows), если процесс запущен из sandbox/agent."""
    global _desktop_attached
    if _desktop_attached:
        return
    try:
        user32 = ctypes.windll.user32
        h_desk = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW(h_desk, 2, buf, 512, ctypes.byref(needed))
        if buf.value.lower() != "default":
            h_def = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if h_def:
                user32.SetThreadDesktop(h_def)
                logger.info(f"Переключен рабочий стол с '{buf.value}' на 'Default'.")
        _desktop_attached = True
    except Exception as e:
        logger.debug(f"attach_desktop error: {e}")

# -------------------------------------------------------------
# Определение окна Codex / ChatGPT и сопоставление данных
# -------------------------------------------------------------
CODEX_PROCESS_NAMES = ("chatgpt.exe", "codex.exe")


class CodexInstance(NamedTuple):
    """Один запущенный Codex (аккаунт): главное окно, PID процесса и его каталог данных CODEX_HOME"""
    hwnd: int
    pid: int
    home: str


def same_home(a, b):
    return os.path.normcase(os.path.normpath(a or CODEX_DIR)) == os.path.normcase(os.path.normpath(b or CODEX_DIR))

def account_label(home):
    """Короткое имя аккаунта по каталогу данных (.codex-account2 -> codex-account2)"""
    return os.path.basename(os.path.normpath(home or CODEX_DIR)).lstrip(".") or "codex"

_home_cache = {}  # pid -> (время создания процесса, CODEX_HOME)

def resolve_codex_home(pid):
    """
    CODEX_HOME процесса Codex. Несколько аккаунтов = несколько копий приложения с разным CODEX_HOME
    (у каждой свои базы thread_history / goals / session_index). Без переменной действует ~/.codex.
    """
    try:
        proc = psutil.Process(pid)
        created = proc.create_time()
    except Exception:
        return CODEX_DIR
    cached = _home_cache.get(pid)
    if cached and cached[0] == created:
        return cached[1]

    try:
        candidates = [proc] + proc.children()  # на случай, если переменная задана только app-server'у
    except Exception:
        candidates = [proc]

    home = None
    read_ok = False
    for p in candidates:
        try:
            env_home = p.environ().get("CODEX_HOME")
            read_ok = True
        except Exception:
            continue
        if env_home:
            home = os.path.abspath(os.path.expandvars(os.path.expanduser(env_home)))
            break
    if not home or not os.path.isdir(home):
        home = CODEX_DIR
    if read_ok:  # не кэшируем результат, если окружение прочитать не удалось
        _home_cache[pid] = (created, home)
    return home

def _enum_codex_windows():
    """
    Окна Codex / ChatGPT: ([(hwnd, pid), ...] видимые либо свёрнутые, [(hwnd, pid), ...] скрытые).
    Скрытое окно (свёрнуто в трей) UIA не отдаёт, поэтому такой аккаунт не наблюдается.
    """
    attach_desktop()
    found_windows = []
    hidden_windows = []

    def enum_cb(h, _):
        try:
            txt = win32gui.GetWindowText(h)
            cls = win32gui.GetClassName(h)
            if cls == "Chrome_WidgetWin_1" and any(k in txt.lower() for k in ("chatgpt", "codex")):
                # Только окна процессов Codex / ChatGPT (исключаем браузеры и IDE с «codex» в заголовке)
                pid = win32process.GetWindowThreadProcessId(h)[1]
                if psutil.Process(pid).name().lower() not in CODEX_PROCESS_NAMES:
                    return True
                if win32gui.IsIconic(h):
                    found_windows.append((h, pid))
                else:
                    rect = win32gui.GetWindowRect(h)
                    big = (rect[2] - rect[0]) > 300 and (rect[3] - rect[1]) > 300
                    if big and win32gui.IsWindowVisible(h):
                        found_windows.append((h, pid))
                    elif big:
                        hidden_windows.append((h, pid))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    return found_windows, hidden_windows

def scan_codex():
    """
    Все запущенные экземпляры Codex (по одному окну на процесс) с их CODEX_HOME:
    (наблюдаемые экземпляры, экземпляры только со скрытым окном)
    """
    visible, hidden = _enum_codex_windows()
    instances, seen = [], set()
    for hwnd, pid in visible:
        if pid not in seen:
            seen.add(pid)
            instances.append(CodexInstance(hwnd, pid, resolve_codex_home(pid)))
    hidden_instances = []
    for hwnd, pid in hidden:
        if pid not in seen:
            seen.add(pid)
            hidden_instances.append(CodexInstance(hwnd, pid, resolve_codex_home(pid)))
    return instances, hidden_instances

def find_codex_instances():
    """Наблюдаемые экземпляры Codex (с видимым или свёрнутым окном)"""
    return scan_codex()[0]

def short_account_name(home):
    """Имя аккаунта для подсказки в трее: default / account2"""
    if same_home(home, CODEX_DIR):
        return "default"
    name = account_label(home)
    for prefix in ("codex-", "codex_"):
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix):]
    return name

def find_codex_window(home=None):
    """Главное окно Codex / ChatGPT; при указании home - окно аккаунта с этим CODEX_HOME"""
    instances = find_codex_instances()
    if home:
        for inst in instances:
            if same_home(inst.home, home):
                return inst.hwnd
        return None  # окно нужного аккаунта не найдено - чужой аккаунт не трогаем
    return instances[0].hwnd if instances else None

def get_thread_name_map(home=None):
    """Сопоставление thread_id -> имя чата из session_index.jsonl"""
    name_map = {}
    index_file = session_index_file(home)
    if not os.path.exists(index_file):
        return name_map
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    tid = obj.get("id") or obj.get("thread_id")
                    title = obj.get("thread_name") or obj.get("title") or obj.get("name")
                    if tid and title:
                        name_map[str(tid).strip()] = title.strip()
                except Exception:
                    pass
    except Exception:
        pass
    return name_map

def get_thread_project_map(home=None):
    """Сопоставление thread_id -> путь к проекту cwd из session_index.jsonl"""
    proj_map = {}
    index_file = session_index_file(home)
    if not os.path.exists(index_file):
        return proj_map
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    tid = obj.get("id") or obj.get("thread_id")
                    cwd = obj.get("cwd")
                    if tid and cwd:
                        norm = os.path.normpath(cwd).replace("\\\\?\\", "")
                        proj_map[str(tid).strip()] = norm
                except Exception:
                    pass
    except Exception:
        pass
    return proj_map

def get_all_recent_goals(home=None):
    """Недавние цели аккаунта из goals_1.sqlite для меню: (имя чата, статус цели, статус хода, thread_id, обновлено, home)"""
    results = []
    home = home or CODEX_DIR
    goals_path = goals_db(home)
    history_path = history_db(home)
    if not os.path.exists(goals_path):
        return results
    try:
        name_map = get_thread_name_map(home)
        conn = sqlite3.connect(f"file:{goals_path}?mode=ro", uri=True, timeout=1.0)
        cursor = conn.cursor()
        one_day_ago = int((time.time() - 24 * 3600) * 1000)
        cursor.execute(
            "SELECT thread_id, status, updated_at_ms, objective FROM thread_goals WHERE updated_at_ms > ? ORDER BY updated_at_ms DESC LIMIT 10",
            (one_day_ago,)
        )
        goals = cursor.fetchall()
        conn.close()

        con_h = None
        if os.path.exists(history_path):
            con_h = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)

        for tid, g_st, upd_ms, obj in goals:
            c_name = name_map.get(str(tid).strip(), f"Чат {str(tid)[:8]}")
            turn_st = "idle"
            if con_h:
                try:
                    cur_h = con_h.cursor()
                    cur_h.execute("SELECT status FROM thread_turns WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1", (tid,))
                    row = cur_h.fetchone()
                    if row:
                        turn_st = row[0]
                except Exception:
                    pass
            results.append((c_name, g_st, turn_st, tid, upd_ms, home))

        if con_h:
            con_h.close()
    except Exception as e:
        logger.debug(f"Ошибка get_all_recent_goals: {e}")
    return results

def get_latest_turn_info(thread_id=None, home=None, thread_ids=None):
    """
    Получение информации о последнем шаге (turn) из thread_history_1.sqlite аккаунта.
    thread_ids - список потоков (чаты с одинаковым названием): берётся самый свежий ход среди них.
    """
    history_path = history_db(home)
    if not os.path.exists(history_path):
        return None, None, None, None
    try:
        conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)
        cursor = conn.cursor()
        if thread_ids:
            marks = ",".join("?" * len(thread_ids))
            cursor.execute(
                f"SELECT thread_id, status, error_json, completed_at FROM thread_turns WHERE thread_id IN ({marks}) ORDER BY rowid DESC LIMIT 1",
                tuple(thread_ids)
            )
        elif thread_id:
            cursor.execute(
                "SELECT thread_id, status, error_json, completed_at FROM thread_turns WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1",
                (thread_id,)
            )
        else:
            cursor.execute(
                "SELECT thread_id, status, error_json, completed_at FROM thread_turns ORDER BY rowid DESC LIMIT 1"
            )
        row = cursor.fetchone()
        conn.close()
        if row:
            tid, status, err_json, completed_at = row
            c_sec = None
            if completed_at:
                c_sec = (completed_at / 1000.0) if completed_at > 1e11 else float(completed_at)
            return tid, status, (err_json or ""), c_sec
    except Exception as e:
        logger.debug(f"Ошибка get_latest_turn_info: {e}")
    return None, None, None, None

USAGE_LIMIT_MARKERS = ("usagelimitexceeded", "usage limit")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
)}
# «try again at Oct 4th, 2026 12:14 AM.» либо «try again at 8:02 PM.»
_USAGE_RESET_RE = re.compile(
    r"try again at\s+(?:([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4}),?\s+)?(\d{1,2}):(\d{2})\s*([AP]M)",
    re.IGNORECASE
)

def is_usage_limit_error(err_json):
    """Ошибка исчерпания лимита использования (5-часовое / недельное окно)"""
    low = (err_json or "").lower()
    return any(k in low for k in USAGE_LIMIT_MARKERS)

def parse_usage_limit_reset(err_json, completed_at=None):
    """Время сброса лимита (unix-время, локальное) из текста ошибки, либо None"""
    m = _USAGE_RESET_RE.search(err_json or "")
    if not m:
        return None
    try:
        mon, day, year, hh, mm, ampm = m.groups()
        hour = int(hh) % 12 + (12 if ampm.upper() == "PM" else 0)
        if mon:
            month = _MONTHS.get(mon[:3].lower())
            if not month:
                return None
            dt = datetime(int(year), month, int(day), hour, int(mm))
        else:
            base = datetime.fromtimestamp(completed_at or time.time())
            dt = base.replace(hour=hour, minute=int(mm), second=0, microsecond=0)
            if dt < base:
                dt += timedelta(days=1)
        return dt.timestamp()
    except Exception:
        return None

USER_IDLE_BEFORE_CHAT_SWITCH = 120.0
RETRY_CAP_COOLDOWN = 600.0   # пауза после лимита повторов подряд, затем попытки продолжаются

def get_user_idle_seconds():
    """Сколько секунд пользователь не трогал клавиатуру и мышь"""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        pass
    return 0.0

def is_thread_in_progress(thread_id, home=None):
    """Проверка, выполняется ли сейчас генерация в указанном потоке"""
    try:
        history_path = history_db(home)
        if not os.path.exists(history_path):
            return False
        conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)
        cur = conn.cursor()
        cur.execute("SELECT status FROM thread_turns WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1", (thread_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] == "inProgress":
            return True
    except Exception:
        pass
    return False

def get_goal_info_for_thread(thread_id=None, home=None):
    """
    Возвращает (chat_name, goal_text, is_explicit_goal) для указанного или последнего активного потока.
    - chat_name: Человекочитаемое название чата из session_index.jsonl (или 'Активный чат')
    - goal_text: Текст цели из thread_goals (или последний запрос пользователя из thread_items)
    - is_explicit_goal: True, если найдена цель в thread_goals
    """
    chat_name = "Активный чат"
    goal_text = ""
    is_explicit_goal = False
    tid_str = str(thread_id).strip() if thread_id else ""

    goals_path = goals_db(home)
    history_path = history_db(home)

    name_map = get_thread_name_map(home)
    if tid_str and tid_str in name_map:
        chat_name = name_map[tid_str]

    # 1. Проверяем thread_goals в goals_1.sqlite
    if os.path.exists(goals_path):
        try:
            conn = sqlite3.connect(f"file:{goals_path}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            if tid_str:
                cur.execute(
                    "SELECT objective FROM thread_goals WHERE thread_id = ? ORDER BY updated_at_ms DESC LIMIT 1",
                    (tid_str,)
                )
            else:
                cur.execute(
                    "SELECT thread_id, objective FROM thread_goals ORDER BY updated_at_ms DESC LIMIT 1"
                )
            row = cur.fetchone()
            if row:
                if not tid_str and len(row) == 2:
                    found_tid = str(row[0]).strip()
                    if found_tid in name_map:
                        chat_name = name_map[found_tid]
                    tid_str = found_tid
                    obj = row[1] or ""
                else:
                    obj = row[0] or ""
                if obj and obj.strip():
                    goal_text = obj.strip()
                    is_explicit_goal = True
            conn.close()
        except Exception as e:
            logger.debug(f"Ошибка чтения thread_goals: {e}")

    # 2. Если цель в thread_goals не найдена, пробуем извлечь последний запрос пользователя из thread_history_1.sqlite
    if not goal_text and tid_str and os.path.exists(history_path):
        try:
            conn = sqlite3.connect(f"file:{history_path}?mode=ro", uri=True, timeout=1.0)
            cur = conn.cursor()
            cur.execute(
                "SELECT item_json FROM thread_items WHERE thread_id = ? AND item_type = 'userMessage' ORDER BY rowid DESC LIMIT 1",
                (tid_str,)
            )
            row = cur.fetchone()
            if row and row[0]:
                data = json.loads(row[0])
                c = data.get("content")
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "text":
                            goal_text = (part.get("text") or "").strip()
                            if goal_text:
                                break
                elif isinstance(c, str):
                    goal_text = c.strip()
            conn.close()
        except Exception as e:
            logger.debug(f"Ошибка чтения userMessage: {e}")

    # 3. Если всё ещё нет текста, но есть имя чата, используем имя чата
    if not goal_text and chat_name and chat_name != "Активный чат":
        goal_text = chat_name

    return chat_name, goal_text, is_explicit_goal


# -------------------------------------------------------------
# Фоновые действия UIAutomation (без смены фокуса и без оконных событий)
# -------------------------------------------------------------
def wake_chromium_accessibility(hwnd):
    """Будит дерево UIAutomation внутри Chromium/Electron без влияния на фокус"""
    try:
        user32 = ctypes.windll.user32
        WM_GETOBJECT = 0x003D
        OBJID_CLIENT = 0xFFFFFFFC
        user32.SendMessageW(hwnd, WM_GETOBJECT, 0, OBJID_CLIENT)

        def enum_child_cb(c, _):
            cls = win32gui.GetClassName(c)
            if "Chrome_RenderWidgetHostHWND" in cls or "Intermediate D3D" in cls:
                user32.SendMessageW(c, WM_GETOBJECT, 0, OBJID_CLIENT)
            return True

        win32gui.EnumChildWindows(hwnd, enum_child_cb, None)
    except Exception:
        pass

def find_composer(uia, root):
    """Поле ввода сообщения Codex (ProseMirror)"""
    from comtypes.gen.UIAutomationClient import TreeScope_Descendants, UIA_ControlTypePropertyId
    composer = None
    edits = root.FindAll(TreeScope_Descendants, uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50004))
    for i in range(edits.Length):
        el = edits.GetElement(i)
        if "prosemirror" in (el.CurrentClassName or "").lower():
            composer = el
    return composer

def flush_stale_uia_tree(uia, root, hwnd):
    """
    У свёрнутого / скрытого окна (в т.ч. при закрытой крышке ноутбука) Chromium отдаёт дерево UIA
    с отставанием на одно действие. Безвредный ScrollIntoView на поле ввода заставляет его
    отдать актуальное состояние, не меняя фокус.
    """
    try:
        if win32gui.GetForegroundWindow() == hwnd:
            return
        from comtypes.gen.UIAutomationClient import UIA_ScrollItemPatternId, IUIAutomationScrollItemPattern
        composer = find_composer(uia, root)
        if not composer:
            return
        pat = composer.GetCurrentPattern(UIA_ScrollItemPatternId)
        if pat:
            pat.QueryInterface(IUIAutomationScrollItemPattern).ScrollIntoView()
            time.sleep(0.4)
    except Exception as e:
        logger.debug(f"Ошибка flush_stale_uia_tree: {e}")

def find_uia_action_buttons(hwnd):
    """Поиск кнопок возобновления, повтора и индикатора выполнения в окне Codex"""
    wake_chromium_accessibility(hwnd)

    from comtypes.client import CreateObject, GetModule
    try:
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_ControlTypePropertyId
        )

        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return None, None, False

        flush_stale_uia_tree(uia, root, hwnd)

        cond_btn = uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50000)
        elems = root.FindAll(TreeScope_Descendants, cond_btn)

        goal_btn = None
        retry_btn = None
        is_running = False

        RESUME_NAMES = {
            "возобновить", "resume", "возобновить цель", "resume goal",
            "продолжить", "продолжить цель", "continue"
        }
        RETRY_NAMES = {
            "повторить", "retry", "попробовать снова", "повторить попытку"
        }
        STOP_NAMES = {
            "остановить", "stop", "прервать"
        }

        for i in range(elems.Length):
            el = elems.GetElement(i)
            name = (el.CurrentName or "").strip().lower()
            if not name:
                continue

            if name in STOP_NAMES:
                is_running = True

            if name in RESUME_NAMES:
                goal_btn = (el, el.CurrentName)
            elif name in RETRY_NAMES or name.startswith("повторить через") or name.startswith("retry in"):
                retry_btn = (el, el.CurrentName)

        return goal_btn, retry_btn, is_running
    except Exception as e:
        logger.debug(f"Ошибка find_uia_action_buttons: {e}")
        return None, None, False

# Сторож никогда не трогает платёжные кнопки и сброс лимитов: reset недельного лимита делает только пользователь
FORBIDDEN_BUTTON_WORDS = ("reset", "сброс", "upgrade", "credit", "purchase", "buy", "купить", "оплат", "тариф")

def invoke_button(btn_tuple):
    """Вызов действия кнопки через UIA чисто в памяти Chromium (без влияния на активные окна)"""
    if not btn_tuple:
        return False
    btn, label = btn_tuple
    if any(w in (label or "").lower() for w in FORBIDDEN_BUTTON_WORDS):
        logger.warning(f"Кнопка '{label}' в списке запрещённых (сброс лимитов / оплата) - нажатие пропущено.")
        return False
    from comtypes.gen.UIAutomationClient import (
        UIA_InvokePatternId,
        IUIAutomationInvokePattern,
        UIA_LegacyIAccessiblePatternId,
        IUIAutomationLegacyIAccessiblePattern,
    )
    try:
        pat = btn.GetCurrentPattern(UIA_InvokePatternId)
        if pat:
            inv = pat.QueryInterface(IUIAutomationInvokePattern)
            inv.Invoke()
            logger.info(f"Действие '{label}' вызвано в фоне через IUIAutomationInvokePattern.")
            return True
    except Exception as e:
        logger.debug(f"InvokePattern failed for {label}: {e}")

    try:
        leg = btn.GetCurrentPattern(UIA_LegacyIAccessiblePatternId)
        if leg:
            leg_p = leg.QueryInterface(IUIAutomationLegacyIAccessiblePattern)
            leg_p.DoDefaultAction()
            logger.info(f"Действие '{label}' вызвано в фоне через DoDefaultAction.")
            return True
    except Exception as e:
        logger.debug(f"DoDefaultAction failed for {label}: {e}")
    return False

# Промо-диалог «Introducing Fast mode»: оставляем стандартную скорость (Fast увеличивает расход лимита)
ALWAYS_DISMISS_DIALOG_BUTTONS = ("Use standard speed",)

def dismiss_blocking_dialog(hwnd, button_names):
    """
    Модальные диалоги Codex («Trust these folders?», «Introducing Fast mode») перекрывают всё окно
    и блокируют возобновление. Нажимает первую найденную кнопку из button_names.
    Возвращает True, если диалог был закрыт.
    """
    try:
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_ControlTypePropertyId
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return False

        wanted = {n.lower(): n for n in button_names}
        target = None
        btns = root.FindAll(TreeScope_Descendants, uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50000))
        for i in range(btns.Length):
            b = btns.GetElement(i)
            if (b.CurrentName or "").strip().lower() in wanted:
                target = b
                break
        # Под модальным диалогом остальной интерфейс недоступен; если поле ввода видно - это не диалог
        if not target or find_composer(uia, root):
            return False

        details = []
        for ctype in (50020, 50007):
            items = root.FindAll(TreeScope_Descendants, uia.CreatePropertyCondition(UIA_ControlTypePropertyId, ctype))
            for i in range(items.Length):
                name = (items.GetElement(i).CurrentName or "").strip()
                if name:
                    details.append(name[:80])

        label = target.CurrentName
        if invoke_button((target, label)):
            logger.info(f"Блокирующий диалог закрыт автоматически кнопкой '{label}': {' | '.join(details[:4]) or '?'}")
            return True
    except Exception as e:
        logger.debug(f"Ошибка dismiss_blocking_dialog: {e}")
    return False

def get_visible_chat_title(hwnd):
    """Название чата, открытого сейчас в окне Codex (имя корневого документа)"""
    try:
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_ControlTypePropertyId
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return None
        doc = root.FindFirst(TreeScope_Descendants, uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50030))
        if doc:
            return (doc.CurrentName or "").strip() or None
    except Exception as e:
        logger.debug(f"Ошибка get_visible_chat_title: {e}")
    return None

def send_chat_message(hwnd, text):
    """Фоновая отправка сообщения в открытый чат: текст в поле ввода + кнопка Send через UIA"""
    wake_chromium_accessibility(hwnd)
    try:
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_ControlTypePropertyId,
            UIA_ValuePatternId,
            IUIAutomationValuePattern,
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return False

        composer = find_composer(uia, root)
        if not composer:
            logger.debug("Поле ввода Codex не найдено.")
            return False

        val = composer.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(IUIAutomationValuePattern)
        val.SetValue(text)

        # Кнопка Send становится активной, только когда текст принят и лимит действительно сброшен
        cond_btn = uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50000)
        deadline = time.time() + 10.0
        while time.time() < deadline:
            time.sleep(0.5)
            flush_stale_uia_tree(uia, root, hwnd)
            btns = root.FindAll(TreeScope_Descendants, cond_btn)
            for i in range(btns.Length):
                b = btns.GetElement(i)
                if (b.CurrentName or "").strip().lower() in ("send", "отправить") and b.CurrentIsEnabled:
                    return invoke_button((b, b.CurrentName))

        val.SetValue("")
        return False
    except Exception as e:
        logger.debug(f"Ошибка send_chat_message: {e}")
        return False

def usage_limit_banner_visible(hwnd):
    """
    Виден ли баннер исчерпанного лимита у поля ввода («You're out of Codex and Work usage» /
    «Your rate limit resets on ...» / кнопка «Reset usage»). Только чтение: кнопки сброса не трогаем.
    Возвращает None, если дерево UIA недоступно (тогда выводов не делаем).
    """
    try:
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_ControlTypePropertyId,
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return None
        for ctype in (50020, 50000):  # Text, Button
            els = root.FindAll(TreeScope_Descendants, uia.CreatePropertyCondition(UIA_ControlTypePropertyId, ctype))
            for i in range(els.Length):
                name = (els.GetElement(i).CurrentName or "").strip().lower()
                if not name:
                    continue
                if ctype == 50000 and name == "reset usage":
                    return True
                if ctype == 50020 and ("rate limit resets" in name or "out of codex" in name):
                    return True
        # Баннера нет. «Лимит снят» утверждаем, только если интерфейс чата виден (есть поле ввода):
        # при модальном диалоге или неготовом дереве UIA судить нельзя
        return False if find_composer(uia, root) else None
    except Exception as e:
        logger.debug(f"Ошибка usage_limit_banner_visible: {e}")
        return None

def composer_is_empty(hwnd):
    """Поле ввода пустое (UIA отдаёт placeholder как значение - это тоже пустое поле). При сомнении - False"""
    try:
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            UIA_ValuePatternId,
            IUIAutomationValuePattern,
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        composer = find_composer(uia, root) if root else None
        if not composer:
            return False
        val = composer.GetCurrentPattern(UIA_ValuePatternId).QueryInterface(IUIAutomationValuePattern)
        text = (val.CurrentValue or "").strip()
        return (not text) or text == (composer.CurrentName or "").strip()
    except Exception as e:
        logger.debug(f"Ошибка composer_is_empty: {e}")
        return False

def switch_to_sidebar_chat(hwnd, target_chat_name):
    """Фоновое переключение чата в боковом меню через UIAutomation"""
    try:
        wake_chromium_accessibility(hwnd)
        from comtypes.client import CreateObject, GetModule
        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation,
            IUIAutomation,
            TreeScope_Descendants,
            UIA_NamePropertyId,
            UIA_ControlTypePropertyId,
            UIA_InvokePatternId,
            IUIAutomationInvokePattern,
            UIA_LegacyIAccessiblePatternId,
            IUIAutomationLegacyIAccessiblePattern,
        )
        uia = CreateObject(CUIAutomation, interface=IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return False

        cond_name = uia.CreatePropertyCondition(UIA_NamePropertyId, target_chat_name)
        cond_type = uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50000)
        cond_and = uia.CreateAndCondition(cond_name, cond_type)
        btn = root.FindFirst(TreeScope_Descendants, cond_and)

        if not btn:
            cond_btn = uia.CreatePropertyCondition(UIA_ControlTypePropertyId, 50000)
            all_btns = root.FindAll(TreeScope_Descendants, cond_btn)
            t_low = target_chat_name.lower()
            for i in range(all_btns.Length):
                b = all_btns.GetElement(i)
                b_name = (b.CurrentName or "").lower()
                if t_low in b_name or b_name in t_low:
                    btn = b
                    break

        if btn:
            try:
                pat = btn.GetCurrentPattern(UIA_InvokePatternId)
                if pat:
                    inv = pat.QueryInterface(IUIAutomationInvokePattern)
                    inv.Invoke()
                    logger.info(f"Переключено на чат '{target_chat_name}' через InvokePattern.")
                    return True
            except Exception:
                pass

            try:
                leg = btn.GetCurrentPattern(UIA_LegacyIAccessiblePatternId)
                if leg:
                    leg_p = leg.QueryInterface(IUIAutomationLegacyIAccessiblePattern)
                    leg_p.DoDefaultAction()
                    logger.info(f"Переключено на чат '{target_chat_name}' через DoDefaultAction.")
                    return True
            except Exception:
                pass
        return False
    except Exception as e:
        logger.debug(f"Ошибка переключения на чат '{target_chat_name}': {e}")
        return False

# -------------------------------------------------------------
# Фоновый рабочий поток сторожа
# -------------------------------------------------------------
class _InstanceState:
    """Состояние сторожа для одного экземпляра Codex (аккаунта): у каждого свои повторы, паузы и таймеры"""
    def __init__(self):
        self.consecutive_retries = 0
        self.next_check = 0.0          # не опрашивать экземпляр раньше этого времени (cooldown / пауза)
        self.resume_deadline = None    # когда можно нажимать кнопку после «паузы перед возобновлением»
        self.usage_wait_key = None
        self.usage_sent_key = None
        self.usage_next_try = 0.0
        self.status = "watching"       # watching / generating / button / limit - для статуса в трее
        self.limit_banner_key = None   # ход, для которого уже виден баннер «лимит исчерпан»
        self.banner_gone = 0           # сколько проходов подряд баннер не виден
        self.cap_until = None          # до какого времени ждём после лимита повторов подряд


class WatchdogWorker(QObject):
    goals_updated = pyqtSignal(list)
    resume_triggered = pyqtSignal(str)
    stats_updated = pyqtSignal(int)
    state_changed = pyqtSignal(bool)
    monitor_updated = pyqtSignal(object)   # снимок наблюдения: что именно сторож видит прямо сейчас

    def __init__(self):
        super().__init__()
        self.is_enabled = True
        self.resumes_count = 0
        self.force_resume_flag = False
        self.last_pids = None          # PID экземпляров Codex на прошлом проходе (None - ещё не опрашивали)
        self._states = {}              # pid -> _InstanceState
        self._running = True

    def _retry_cap_blocked(self, st, max_retries, tag=""):
        """
        Лимит повторов подряд достигнут (сервер перегружен и т.п.). Вместо ожидания пользователя до утра
        ждём CAP_COOLDOWN секунд и начинаем счёт заново. True - пока ещё ждём.
        """
        if max_retries <= 0 or st.consecutive_retries < max_retries:
            st.cap_until = None
            return False
        now = time.time()
        if st.cap_until is None:
            st.cap_until = now + RETRY_CAP_COOLDOWN
            logger.warning(
                f"Достигнут лимит повторов подряд ({max_retries}). Пауза {RETRY_CAP_COOLDOWN / 60:.0f} мин, затем продолжение.{tag}"
            )
            return True
        if now < st.cap_until:
            return True
        st.cap_until = None
        st.consecutive_retries = 0
        logger.info(f"Пауза после лимита повторов закончилась - возобновление попыток.{tag}")
        return False

    def _reset_retries(self):
        for st in list(self._states.values()):
            st.consecutive_retries = 0

    def toggle(self):
        self.is_enabled = not self.is_enabled
        self._reset_retries()
        self.state_changed.emit(self.is_enabled)
        logger.info(f"Авто-возобновление переключено: {'ВКЛЮЧЕНО' if self.is_enabled else 'ВЫКЛЮЧЕНО (пауза)'}")
        return self.is_enabled

    def force_resume(self):
        self.force_resume_flag = True
        self._reset_retries()
        logger.info("Ручной вызов возобновления цели.")

    def stop(self):
        self._running = False

    def _watch_scope(self, inst, s):
        """
        Какие чаты учитывать для окна аккаунта: (разрешено, [thread_id] либо None, название открытого чата).
        - обычный режим: любой чат, решение по самому свежему ходу аккаунта;
        - watch_visible_chat_only: решение по ходу открытого чата;
        - watch_selected_chats_only: действуем только в открытом чате, который выбран пользователем
          (чаты по расписанию, авто-ревью и прочие параллельные чаты не мешают и не возобновляются).
        Если открытый чат определить не удалось, в режиме «открытого чата» используется ход аккаунта,
        в режиме «выбранных» ничего не делаем.
        """
        selected_mode = bool(s.get("watch_selected_chats_only", False))
        if not (selected_mode or s.get("watch_visible_chat_only", False)):
            return True, None, None

        title = get_visible_chat_title(inst.hwnd)
        tids = []
        if title:
            wanted = title.strip().lower()
            names = {c["id"]: c["title"] for c in read_chat_catalog(inst.home)}
            for tid, name in get_thread_name_map(inst.home).items():
                names.setdefault(tid, name)
            tids = [t for t, n in names.items() if (n or "").strip().lower() == wanted]

        if selected_mode:
            chosen = {str(x) for x in s.get("selected_chats", [])}
            tids = [t for t in tids if t in chosen]
            return bool(tids), (tids or None), title
        return True, (tids or None), title

    def _latest_turn(self, inst, s, tids=None):
        """Последний ход для решения о возобновлении: среди tids (если заданы), иначе самый свежий ход аккаунта"""
        if tids:
            row = get_latest_turn_info(home=inst.home, thread_ids=tids)
            if row[0] or s.get("watch_selected_chats_only", False):
                return row
        return get_latest_turn_info(home=inst.home)

    def try_usage_limit_resume(self, inst, st, s, max_retries, tag="", tids=None):
        """
        Лимит использования исчерпан: кнопок Resume/Retry нет, поэтому после времени сброса
        отправляем в открытый чат сообщение-продолжение. Возвращает True, если сообщение отправлено.
        """
        hwnd, home = inst.hwnd, inst.home
        tid, turn_status, err_json, completed_at = self._latest_turn(inst, s, tids)
        if turn_status != "failed" or not is_usage_limit_error(err_json):
            return False
        if not settings_mgr.is_error_allowed(err_json):
            return False

        turn_key = (tid, completed_at)
        if turn_key == st.usage_sent_key:
            return False

        thread_cwd = get_thread_project_map(home).get(str(tid).strip()) if tid else None
        if thread_cwd and not settings_mgr.is_project_enabled(thread_cwd):
            return False

        reset_ts = parse_usage_limit_reset(err_json, completed_at)
        chat_name = get_thread_name_map(home).get(str(tid).strip()) if tid else None
        if turn_key != st.usage_wait_key:
            st.usage_wait_key = turn_key
            st.usage_next_try = 0.0
            if reset_ts:
                logger.info(
                    f"Лимит использования исчерпан | Чат: «{chat_name or 'Активный чат'}». "
                    f"Продолжение после сброса: {datetime.fromtimestamp(reset_ts):%d.%m %H:%M}.{tag}"
                )
            else:
                logger.warning(f"Лимит использования исчерпан, время сброса в ошибке не найдено. Будет выполняться проверка снятия лимита.{tag}")
        st.status = "limit"

        now = time.time()
        margin = float(s.get("usage_limit_resume_margin_seconds", 60.0))
        if now < st.usage_next_try:
            return False
        if self._retry_cap_blocked(st, max_retries, tag):
            return False

        # По тексту ошибки лимит ещё не сброшен. Пользователь мог сбросить его вручную (reset),
        # поэтому осторожно проверяем, не стала ли кнопка Send активной: только если он не за компьютером
        # и поле ввода пустое (чтобы не затереть черновик). Пока лимит активен, Send неактивна и ничего не уходит.
        probing = reset_ts is None or now < reset_ts + margin
        if probing:
            if not s.get("usage_limit_probe_enabled", True):
                return False
            if get_user_idle_seconds() < USER_IDLE_BEFORE_CHAT_SWITCH:
                return False
            if not composer_is_empty(hwnd):
                st.usage_next_try = now + 60.0
                return False

        # Сообщение уходит в открытый чат, поэтому он должен совпадать с прерванным
        visible = get_visible_chat_title(hwnd)
        if chat_name and visible and chat_name.strip().lower() != visible.strip().lower():
            if probing:
                st.usage_next_try = now + 120.0   # проверку не делаем за счёт смены чата пользователя
                return False
            # Пользователь не за компьютером -> возвращаемся в прерванный чат сами
            if get_user_idle_seconds() >= USER_IDLE_BEFORE_CHAT_SWITCH and switch_to_sidebar_chat(hwnd, chat_name):
                logger.info(f"Лимит сброшен, открыт другой чат («{visible}»). Переключение на «{chat_name}».{tag}")
                st.usage_next_try = now + 5.0
                return False
            logger.info(f"Лимит сброшен, но открыт другой чат («{visible}» вместо «{chat_name}»). Продолжение отложено.{tag}")
            st.usage_next_try = now + 60.0
            return False

        message = str(s.get("usage_limit_resume_message", "continue")).strip() or "continue"
        if not send_chat_message(hwnd, message):
            if probing:
                st.usage_next_try = now + 120.0   # лимит ещё активен (Send неактивна): тихо повторим позже
                return False
            logger.warning(f"Лимит сброшен, но отправить сообщение не удалось (кнопка Send неактивна). Повтор через 60 сек.{tag}")
            st.usage_next_try = now + 60.0
            return False

        if probing:
            logger.info(f"Лимит снят досрочно (ручной сброс): кнопка Send стала активной.{tag}")
            st.usage_next_try = now + 300.0
        st.usage_sent_key = turn_key
        self.resumes_count += 1
        st.consecutive_retries += 1
        self.stats_updated.emit(self.resumes_count)
        self.resume_triggered.emit("ОТВЕТ")
        acct = f" | Аккаунт: {account_label(home)}" if tag else ""
        logger.info(
            f">>> ВОЗОБНОВЛЕНИЕ (ЛИМИТ) | Чат: «{chat_name or visible or 'Активный чат'}» | "
            f"Отправлено сообщение: «{message}»{acct}! Всего: {self.resumes_count} <<<"
        )
        return True

    def _process_instance(self, inst, st, s, force, tag=""):
        """
        Один проход сторожа по экземпляру Codex (аккаунту). Все ожидания неблокирующие
        (st.next_check / st.resume_deadline), чтобы один аккаунт не задерживал другой.
        Возвращает True, если ручное возобновление (force) обработано этим экземпляром.
        """
        poll_interval = float(s.get("poll_interval_seconds", 1.0))
        retry_pause_cfg = float(s.get("retry_pause_seconds", 10.0))
        cooldown_cfg = float(s.get("post_resume_cooldown", 6.0))
        max_retries = int(s.get("max_retries_consecutive", 10))
        hwnd, home = inst.hwnd, inst.home

        def defer(delay):
            st.next_check = time.time() + delay

        goal_btn, retry_btn, is_running = find_uia_action_buttons(hwnd)
        st.status = "button" if (goal_btn or retry_btn) else ("generating" if is_running else "watching")

        # Режимы «открытый чат» / «выбранные чаты»: какой чат сейчас под наблюдением
        allowed, tids, open_title = self._watch_scope(inst, s)
        if not allowed and open_title:
            st.status = "skipped"   # открытый чат не отмечен пользователем - не трогаем

        if is_running and not (goal_btn or retry_btn):
            # Задача генерируется прямо сейчас
            st.consecutive_retries = 0
            st.resume_deadline = None
            return False

        if not (goal_btn or retry_btn):
            st.resume_deadline = None

        if goal_btn or retry_btn or force:
            if not allowed and not force:
                st.resume_deadline = None
                return False   # чат не выбран; ручное возобновление (force) выполняется всегда

            tid, turn_status, err_json, completed_at = self._latest_turn(inst, s, tids if allowed else None)
            proj_map = get_thread_project_map(home)
            thread_cwd = proj_map.get(str(tid).strip()) if tid else None

            # 1. Проверка фильтра проектов
            if thread_cwd and not settings_mgr.is_project_enabled(thread_cwd):
                logger.info(f"Проект '{thread_cwd}' исключён в настройках. Авто-возобновление пропущено.{tag}")
                defer(poll_interval * 2)
                return force

            # 2. Проверка фильтра ошибок (авто-возобновление ТОЛЬКО при наличии отслеживаемой ошибки)
            if not force:
                if not err_json or turn_status in ("interrupted", "completed"):
                    # Ручная остановка пользователем или нормальное завершение -> НЕ возобновлять
                    return False

                if not (hasattr(settings_mgr, 'is_error_allowed') and settings_mgr.is_error_allowed(err_json)):
                    logger.info(f"Ошибка '{err_json[:60]}' отключена в настройках или не отслеживается. Авто-возобновление пропущено.{tag}")
                    defer(poll_interval * 2)
                    return False

                # До сброса лимита использования нажимать кнопку бессмысленно. Но пользователь может
                # сбросить лимит вручную раньше: тогда баннер у поля ввода исчезает -> продолжаем
                if is_usage_limit_error(err_json):
                    reset_ts = parse_usage_limit_reset(err_json, completed_at)
                    margin = float(s.get("usage_limit_resume_margin_seconds", 60.0))
                    if reset_ts and time.time() < reset_ts + margin:
                        turn_key = (tid, completed_at)
                        banner = usage_limit_banner_visible(hwnd)
                        if banner:
                            st.limit_banner_key = turn_key
                            st.banner_gone = 0
                        elif banner is False and st.limit_banner_key == turn_key:
                            st.banner_gone += 1
                        if not (banner is False and st.limit_banner_key == turn_key and st.banner_gone >= 3):
                            defer(poll_interval * 5)
                            return False
                        logger.info(f"Лимит снят вручную пользователем (баннер лимита исчез) - возобновление без ожидания сброса.{tag}")

            # 3. Лимит повторов подряд
            if self._retry_cap_blocked(st, max_retries, tag):
                defer(poll_interval * 5)
                return force

            is_goal = (goal_btn is not None)
            mode_name = "ЦЕЛЬ" if is_goal else "ОТВЕТ"
            btn_chosen = goal_btn if goal_btn else retry_btn

            # Пауза перед нажатием: при первом обнаружении ставим дедлайн, дальше ждём без блокировки
            now = time.time()
            if force:
                pause_left = 0.0
            elif st.resume_deadline is not None:
                pause_left = max(0.0, st.resume_deadline - now)
            elif completed_at and (now - completed_at) < retry_pause_cfg:
                pause_left = max(0.5, retry_pause_cfg - (now - completed_at))
            else:
                pause_left = 1.0

            if pause_left > 0:
                if st.resume_deadline is None:
                    btn_name_disp = btn_chosen[1] if btn_chosen else "ручной запуск"
                    logger.info(f"Обнаружена кнопка '{btn_name_disp}'. Ожидание паузы {pause_left:.1f} сек...{tag}")
                    st.resume_deadline = now + pause_left
                return force
            st.resume_deadline = None

            # Повторная проверка кнопок перед кликом
            goal_btn, retry_btn, is_running = find_uia_action_buttons(hwnd)
            if is_running:
                return force

            target_btn = goal_btn if goal_btn else retry_btn
            if target_btn:
                btn_name = target_btn[1]
                success = invoke_button(target_btn)
                if success:
                    self.resumes_count += 1
                    st.consecutive_retries += 1
                    self.stats_updated.emit(self.resumes_count)
                    self.resume_triggered.emit(mode_name)

                    chat_name, goal_obj, is_explicit = get_goal_info_for_thread(tid, home=home)
                    goal_clean = " ".join(goal_obj.split()) if goal_obj else ""
                    goal_summary = goal_clean[:120] if goal_clean else ("Цель Codex" if is_goal else "Задача диалога")
                    item_type = "ЦЕЛЬ" if (is_explicit or is_goal) else "ЗАДАЧА"
                    acct = f" | Аккаунт: {account_label(home)}" if tag else ""

                    logger.info(
                        f">>> ВОЗОБНОВЛЕНИЕ ({item_type}) | Чат: «{chat_name}» | {item_type.capitalize()}: «{goal_summary}» "
                        f"(кнопка: '{btn_name}'){acct}! Всего: {self.resumes_count} <<<"
                    )

                    # Звуковое оповещение (если включено в настройках)
                    if s.get("sound_on_resume", False):
                        try:
                            ctypes.windll.user32.MessageBeep(0x00000040)
                        except Exception:
                            pass

                    defer(cooldown_cfg)
            return force

        if not is_running:
            dialog_buttons = list(ALWAYS_DISMISS_DIALOG_BUTTONS)
            if s.get("auto_trust_folders", False):
                dialog_buttons.append("Trust folders")
            if dismiss_blocking_dialog(hwnd, dialog_buttons):
                defer(poll_interval * 2)
                return False
            if allowed and self.try_usage_limit_resume(inst, st, s, max_retries, tag, tids):
                defer(cooldown_cfg)
        return False

    def run_loop(self):
        attach_desktop()
        logger.info("==================================================")
        logger.info("Codex Auto-Resume Tray App запущен.")
        logger.info("Режим: Настройки, мультиязычность, 0 оконных сбоев.")
        logger.info("==================================================")

        while self._running:
            try:
                s = settings_mgr.settings
                poll_interval = float(s.get("poll_interval_seconds", 1.0))
                enable_on_codex_start = bool(s.get("enable_on_codex_start", True))

                instances, hidden_instances = scan_codex()
                pids = {inst.pid for inst in instances}
                multi = len(instances) > 1

                for inst in instances:
                    register_codex_home(inst.home)
                    if inst.pid not in self._states:
                        self._states[inst.pid] = _InstanceState()
                        logger.info(
                            f"Обнаружен Codex | Аккаунт: {account_label(inst.home)} | PID: {inst.pid} | Данные: {inst.home}"
                        )
                for pid in [p for p in self._states if p not in pids]:
                    self._states.pop(pid, None)

                # Цели всех запущенных аккаунтов (если Codex не запущен - аккаунт по умолчанию)
                homes = []
                for inst in instances:
                    if not any(same_home(h, inst.home) for h in homes):
                        homes.append(inst.home)
                goals = []
                for h in (homes or [CODEX_DIR]):
                    goals.extend(get_all_recent_goals(h))
                goals.sort(key=lambda g: g[4] or 0, reverse=True)
                self.goals_updated.emit(goals[:10])

                # Обработка настройки «Включать при старте Кодекса»: появился новый экземпляр Codex
                if enable_on_codex_start and self.last_pids is not None and (pids - self.last_pids):
                    if not self.is_enabled:
                        self.is_enabled = True
                        self._reset_retries()
                        self.state_changed.emit(True)
                        logger.info("Обнаружен запуск Кодекса -> авто-возобновление автоматически ВКЛЮЧЕНО.")
                self.last_pids = pids

                if self.is_enabled and instances:
                    now = time.time()
                    force = self.force_resume_flag
                    force_used = False
                    for inst in instances:
                        st = self._states.get(inst.pid)
                        if st is None or now < st.next_check:
                            continue
                        tag = f" [{account_label(inst.home)}]" if (multi or not same_home(inst.home, CODEX_DIR)) else ""
                        try:
                            if self._process_instance(inst, st, s, force, tag):
                                force_used = True
                        except Exception as e:
                            st.status = "error"
                            logger.error(f"Исключение при обработке Codex ({account_label(inst.home)}): {e}", exc_info=True)
                    if force_used:
                        self.force_resume_flag = False

                # Снимок для трея: какие аккаунты реально под наблюдением и что в них происходит
                self.monitor_updated.emit({
                    "instances": [(inst.home, self._states[inst.pid].status) for inst in instances if inst.pid in self._states],
                    "hidden": [inst.home for inst in hidden_instances],
                    "ts": time.time(),
                })

                time.sleep(poll_interval)
            except Exception as e:
                logger.error(f"Исключение в worker loop: {e}", exc_info=True)
                time.sleep(1.5)

# -------------------------------------------------------------
# Менеджер системного трея (Нативный Win32 TrackPopupMenu)
# -------------------------------------------------------------
class CodexTrayManager(QObject):
    def __init__(self, worker):
        super().__init__()
        self.worker = worker
        self.recent_goals = []
        self.settings_dialog = None
        self.monitor = {"instances": [], "hidden": [], "ts": 0.0}
        self._icon_state = None
        self._tip_core = None
        self._tip_ts = 0.0

        # Скрытый виджет для привязки сообщений меню Win32
        self.dummy = QWidget()
        self.hwnd = int(self.dummy.winId())

        self.init_tray()

        # Сигналы
        self.worker.goals_updated.connect(self.on_goals_updated)
        self.worker.stats_updated.connect(self.on_stats_updated)
        self.worker.resume_triggered.connect(self.on_resume_triggered)
        self.worker.state_changed.connect(self.on_state_changed)
        self.worker.monitor_updated.connect(self.on_monitor_updated)

        # Таймер проверки клика по ярлыку на рабочем столе
        self.trigger_timer = QTimer(self)
        self.trigger_timer.timeout.connect(self.check_toggle_trigger)
        self.trigger_timer.start(300)

        # Стартовое уведомление (только один раз при запуске приложения)
        QTimer.singleShot(500, self.show_initial_notification)

        # Фоновый предзапуск окна настроек (для мгновенного открытия без задержек)
        QTimer.singleShot(1500, self._prewarm_settings_window)

    def _prewarm_settings_window(self):
        try:
            if not hasattr(self, "settings_win") or self.settings_win is None:
                self.settings_win = SettingsWindow()
                self.settings_win.settings_saved.connect(self.on_settings_saved)
                logger.info("Окно настроек предварительно загружено в фоне.")
        except Exception as e:
            logger.debug(f"Prewarm settings window error: {e}")

    def create_tray_icon(self, state="active"):
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Подложка с закругленными углами
        p.setBrush(QBrush(QColor(30, 32, 44)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(2, 2, 60, 60, 14, 14)

        # Основной официальный векторный логотип Codex
        base_file = os.path.join(APP_DIR, "codex_base.png")
        if not os.path.exists(base_file):
            base_file = os.path.join(CODEX_DIR, "codex_base.png")
        if os.path.exists(base_file):
            base_pix = QPixmap(base_file)
            p.drawPixmap(QRect(6, 6, 48, 48), base_pix)

        color_map = {
            "active": QColor(16, 185, 129),
            "paused": QColor(100, 116, 139),
            "working": QColor(14, 165, 233),
            "warn": QColor(245, 158, 11)
        }
        color = color_map.get(state, QColor(16, 185, 129))

        # Окантовка и круг статуса в правом нижнем углу
        p.setBrush(QBrush(color))
        p.setPen(QPen(QColor(30, 32, 44), 3.5))
        p.drawEllipse(34, 34, 27, 27)

        if state == "active":
            p.setPen(QPen(QColor(255, 255, 255), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawLine(41, 48, 46, 53)
            p.drawLine(46, 53, 54, 42)
        elif state == "paused":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255)))
            p.drawRoundedRect(42, 42, 4, 11, 1, 1)
            p.drawRoundedRect(50, 42, 4, 11, 1, 1)
        elif state == "working":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255)))
            p.drawPolygon(QPolygon([QPoint(44, 41), QPoint(55, 47), QPoint(44, 53)]))
        elif state == "warn":
            # Восклицательный знак: сторож включён, но Codex не найден либо не все аккаунты наблюдаются
            p.setPen(QPen(QColor(255, 255, 255), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(47, 41, 47, 49)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255)))
            p.drawEllipse(45, 52, 4, 4)

        p.end()
        return QIcon(pix)

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.update_tray_visuals()
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_monitor_updated(self, snapshot):
        """Сторож каждую секунду сообщает, какие аккаунты Codex он реально видит и что в них происходит"""
        self.monitor = snapshot
        self.update_tray_visuals()

    def update_tray_visuals(self, state=None):
        enabled = self.worker.is_enabled
        if state is None:
            if not enabled:
                state = "paused"
            elif time.time() < getattr(self, "_working_until", 0.0):
                state = "working"
            elif not self.monitor.get("instances") or self.monitor.get("hidden"):
                state = "warn"      # ничего не наблюдается либо часть аккаунтов (скрытое окно) вне наблюдения
            else:
                state = "active"
        if state != self._icon_state:
            self._icon_state = state
            self.tray.setIcon(self.create_tray_icon(state))

        tr = settings_mgr.tr
        lines = [tr("tip_enabled") if enabled else tr("tip_disabled")]

        # Что именно наблюдается: аккаунт -> состояние (наблюдаю / генерация / кнопка / ждёт лимит)
        status_keys = {
            "watching": "tip_st_watching", "generating": "tip_st_generating",
            "button": "tip_st_button", "limit": "tip_st_limit", "error": "tip_st_error",
            "skipped": "tip_st_skipped",
        }
        items = [f"{short_account_name(home)}: {tr(status_keys.get(st, 'tip_st_watching'))}"
                 for home, st in self.monitor.get("instances", [])]
        items += [f"{short_account_name(home)}: {tr('tip_st_hidden')}" for home in self.monitor.get("hidden", [])]
        lines.append(" | ".join(items) if items else tr("tip_no_codex"))

        goals_line = None
        if self.recent_goals:
            active_names = [g[0] for g in self.recent_goals if g[1] in ("active", "blocked") or g[2] == "inProgress"]
            if active_names:
                goals_line = tr("tip_goals", count=len(active_names), names=", ".join(active_names[:2]))

        ts = self.monitor.get("ts", 0.0)
        checked = f" · ✓ {time.strftime('%H:%M:%S', time.localtime(ts))}" if ts else ""
        resumes = tr("tip_resumes", count=self.worker.resumes_count)

        def build(tail):
            # У подсказки трея Windows лимит ~127 символов: при нехватке места отбрасываем строку с целями
            text = "\n".join(lines + ([goals_line] if goals_line else []) + [tail])
            if len(text) > 127:
                text = "\n".join(lines + [tail])
            return text[:127]

        tip = build(resumes + checked)

        # Не дёргаем подсказку каждую секунду: только при изменении содержимого либо раз в 5 сек (время проверки)
        core = build(resumes)
        now = time.time()
        if core != self._tip_core or now - self._tip_ts >= 5.0:
            self._tip_core = core
            self._tip_ts = now
            self.tray.setToolTip(tip)

    def show_initial_notification(self):
        tr = settings_mgr.tr
        st = "ВКЛЮЧЕНО 🟢" if self.worker.is_enabled else "ВЫКЛЮЧЕНО ⚪"
        if settings_mgr.get_effective_language() == "en":
            st = "ENABLED 🟢" if self.worker.is_enabled else "DISABLED ⚪"
        self.tray.showMessage(
            tr("notif_app_title"),
            tr("notif_started", state=st),
            QSystemTrayIcon.MessageIcon.Information,
            3500
        )

    def on_user_toggle(self):
        is_on = self.worker.toggle()
        self.handle_state_notification(is_on)

    def on_state_changed(self, is_on):
        self.update_tray_visuals()

    def handle_state_notification(self, is_on):
        if not settings_mgr.settings.get("notify_on_toggle", True):
            return
        tr = settings_mgr.tr
        if is_on:
            self.tray.showMessage(
                tr("notif_app_title"),
                tr("notif_state_on"),
                QSystemTrayIcon.MessageIcon.Information,
                2500
            )
        else:
            self.tray.showMessage(
                tr("notif_app_title"),
                tr("notif_state_off"),
                QSystemTrayIcon.MessageIcon.Warning,
                2500
            )

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.on_user_toggle()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            self.show_native_menu()

    def show_native_menu(self):
        """Отображение нативного контекстного меню Windows 11 через Win32 API с мультиязычностью"""
        hmenu = win32gui.CreatePopupMenu()
        tr = settings_mgr.tr

        # 1. Тумблер Вкл / Выкл
        toggle_text = tr("menu_auto_resume_on") if self.worker.is_enabled else tr("menu_auto_resume_off")
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 101, toggle_text)
        win32gui.AppendMenu(hmenu, win32con.MF_SEPARATOR, 0, "")

        # 2. Динамическое подменю целей Codex
        hsub = win32gui.CreatePopupMenu()
        goals = self.recent_goals or get_all_recent_goals()
        if not goals:
            win32gui.AppendMenu(hsub, win32con.MF_GRAYED | win32con.MF_DISABLED, 200, tr("menu_no_goals"))
        else:
            for i, g in enumerate(goals):
                c_name, g_status, turn_st, tid, ts, g_home = g
                if not same_home(g_home, CODEX_DIR):
                    c_name = f"{c_name} [{account_label(g_home)}]"
                if turn_st == "inProgress":
                    st_icon = "🟡"
                elif g_status in ("blocked", "paused") or turn_st == "failed":
                    st_icon = "🔴"
                else:
                    st_icon = "🟢"
                win32gui.AppendMenu(hsub, win32con.MF_STRING, 201 + i, f"{c_name} — {st_icon} {g_status}")
        win32gui.AppendMenu(hmenu, win32con.MF_POPUP, hsub, tr("menu_goals_submenu"))

        # 3. Принудительное возобновление
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 102, tr("menu_force_resume"))
        win32gui.AppendMenu(hmenu, win32con.MF_SEPARATOR, 0, "")

        # 4. Настройки (НОВЫЙ ПУНКТ)
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 103, tr("menu_settings"))

        # 5. Статистика
        win32gui.AppendMenu(hmenu, win32con.MF_GRAYED | win32con.MF_DISABLED, 107, tr("menu_resumes_count", count=self.worker.resumes_count))

        # 6. Журнал и папка
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 104, tr("menu_open_log"))
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 105, tr("menu_open_folder"))

        # 7. Автозапуск при старте Windows
        autostart_flags = win32con.MF_STRING
        if os.path.exists(AUTOSTART_VBS):
            autostart_flags |= win32con.MF_CHECKED
        else:
            autostart_flags |= win32con.MF_UNCHECKED
        win32gui.AppendMenu(hmenu, autostart_flags, 106, tr("menu_autostart_windows"))

        win32gui.AppendMenu(hmenu, win32con.MF_SEPARATOR, 0, "")

        # 8. Выход
        win32gui.AppendMenu(hmenu, win32con.MF_STRING, 109, tr("menu_exit"))

        pos = win32gui.GetCursorPos()
        win32gui.SetForegroundWindow(self.hwnd)
        cmd = win32gui.TrackPopupMenu(
            hmenu,
            win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON | win32con.TPM_RETURNCMD,
            pos[0], pos[1], 0, self.hwnd, None
        )
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(hmenu)

        if cmd == 101:
            self.on_user_toggle()
        elif cmd == 102:
            self.on_force_resume()
        elif cmd == 103:
            QTimer.singleShot(20, self.open_settings_dialog)
        elif cmd == 104:
            self.on_open_log()
        elif cmd == 105:
            self.on_open_folder()
        elif cmd == 106:
            self.on_toggle_autostart(not os.path.exists(AUTOSTART_VBS))
        elif cmd == 109:
            self.on_quit()
        elif cmd >= 201 and cmd < 201 + len(goals):
            chosen_goal = goals[cmd - 201]
            self.switch_chat(chosen_goal[0], chosen_goal[5])

    def open_settings_dialog(self):
        """Открытие диалогового окна настроек мгновенно в том же приложении"""
        try:
            logger.info("Открытие окна настроек через SettingsWindow...")
            if not hasattr(self, "settings_win") or self.settings_win is None:
                self.settings_win = SettingsWindow()
                self.settings_win.settings_saved.connect(self.on_settings_saved)
            self.settings_win.load_values()
            self.settings_win.showNormal()
            self.settings_win.raise_()
            self.settings_win.activateWindow()

            hwnd = int(self.settings_win.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 5)  # SW_SHOW
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.error(f"Ошибка открытия SettingsWindow: {e}", exc_info=True)

    def on_settings_saved(self):
        """Реакция на сохранение настроек из диалога"""
        self.update_tray_visuals()

    def check_toggle_trigger(self):
        if os.path.exists(TOGGLE_TRIGGER):
            try:
                os.remove(TOGGLE_TRIGGER)
            except Exception:
                pass
            self.on_user_toggle()

        # Мониторинг изменений файла настроек guardian_settings.json
        try:
            if os.path.exists(SETTINGS_FILE):
                mtime = os.path.getmtime(SETTINGS_FILE)
                if not hasattr(self, "_last_settings_mtime"):
                    self._last_settings_mtime = mtime
                elif mtime > self._last_settings_mtime:
                    self._last_settings_mtime = mtime
                    settings_mgr.load()
                    self.update_tray_visuals()
                    logger.info("Настройки сторожа автоматически перезагружены.")
        except Exception:
            pass

    def on_force_resume(self):
        self.worker.force_resume()
        tr = settings_mgr.tr
        if settings_mgr.settings.get("notify_on_toggle", True):
            self.tray.showMessage(tr("notif_app_title"), tr("notif_force_resume"), QSystemTrayIcon.MessageIcon.Information, 2000)

    def on_open_log(self):
        try:
            if os.path.exists(LOG_FILE):
                os.startfile(LOG_FILE)
        except Exception:
            pass

    def on_open_folder(self):
        try:
            if os.path.exists(CODEX_DIR):
                os.startfile(CODEX_DIR)
        except Exception:
            pass

    def on_toggle_autostart(self, enable):
        tr = settings_mgr.tr
        try:
            if enable:
                pyw = sys.executable.replace("python.exe", "pythonw.exe")
                app_py = os.path.abspath(__file__)
                vbs = f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run """{pyw}"" """{app_py}""", 0, False\n'
                os.makedirs(os.path.dirname(AUTOSTART_VBS), exist_ok=True)
                with open(AUTOSTART_VBS, "w", encoding="utf-8") as f:
                    f.write(vbs)
                settings_mgr.settings["autostart_windows"] = True
                settings_mgr.save()
                logger.info("Автозагрузка сторожа включена.")
                if settings_mgr.settings.get("notify_on_toggle", True):
                    self.tray.showMessage(tr("notif_app_title"), tr("notif_autostart_on"), QSystemTrayIcon.MessageIcon.Information, 2000)
            else:
                if os.path.exists(AUTOSTART_VBS):
                    os.remove(AUTOSTART_VBS)
                settings_mgr.settings["autostart_windows"] = False
                settings_mgr.save()
                logger.info("Автозагрузка сторожа отключена.")
                if settings_mgr.settings.get("notify_on_toggle", True):
                    self.tray.showMessage(tr("notif_app_title"), tr("notif_autostart_off"), QSystemTrayIcon.MessageIcon.Information, 2000)
        except Exception as e:
            logger.error(f"Ошибка автозапуска: {e}")

    def on_goals_updated(self, goals):
        self.recent_goals = goals

    def switch_chat(self, chat_name, home=None):
        hwnd = find_codex_window(home)
        if hwnd:
            user32 = ctypes.windll.user32
            if win32gui.IsIconic(hwnd):
                user32.ShowWindow(hwnd, win32con.SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            switch_to_sidebar_chat(hwnd, chat_name)

    def on_stats_updated(self, count):
        self.update_tray_visuals()

    def on_resume_triggered(self, mode):
        # Бесшумное визуальное обновление в трее
        self._working_until = time.time() + 2.5
        self.update_tray_visuals(state="working")
        QTimer.singleShot(2500, lambda: self.update_tray_visuals())

        # Если пользователь явно включил уведомления при возобновлении:
        if settings_mgr.settings.get("notify_on_resume", False):
            tr = settings_mgr.tr
            self.tray.showMessage(
                tr("notif_app_title"),
                f"⚡ {mode}",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )

    def on_quit(self):
        self.worker.stop()
        if hasattr(self, "settings_win") and self.settings_win:
            try:
                self.settings_win.close()
            except Exception:
                pass
        self.tray.hide()
        QApplication.quit()

# -------------------------------------------------------------
# Точка входа
# -------------------------------------------------------------
def main():
    attach_desktop()

    # Защита от дубликатов
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    mutex = kernel32.CreateMutexW(None, False, "Global\\CodexAutoResumeTrayMutex")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        try:
            with open(TOGGLE_TRIGGER, "w", encoding="utf-8") as f:
                f.write("toggle")
        except Exception:
            pass
        sys.exit(0)

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mrpanica.codex.autoresume.v2")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    icon_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    worker = WatchdogWorker()
    worker_thread = threading.Thread(target=worker.run_loop, daemon=True)
    worker_thread.start()

    tray_mgr = CodexTrayManager(worker)

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
