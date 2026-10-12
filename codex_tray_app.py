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
from settings_gui import settings_mgr, SettingsWindow, get_all_codex_projects

# -------------------------------------------------------------
# Конфигурация путей
# -------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
USER_PROFILE = os.environ.get("USERPROFILE", r"C:\Users\Artur")
CODEX_DIR = os.path.join(USER_PROFILE, ".codex")
os.makedirs(CODEX_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(CODEX_DIR, "guardian_settings.json")
LOG_FILE = os.path.join(CODEX_DIR, "guardian.log")
HISTORY_DB = os.path.join(CODEX_DIR, "thread_history_1.sqlite")
GOALS_DB = os.path.join(CODEX_DIR, "goals_1.sqlite")
SESSION_INDEX_FILE = os.path.join(CODEX_DIR, "session_index.jsonl")
TOGGLE_TRIGGER = os.path.join(CODEX_DIR, "toggle.trigger")
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


def find_codex_window():
    """Поиск главного окна Codex / ChatGPT (включая свёрнутое или открытое)"""
    attach_desktop()
    found_windows = []

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
                    found_windows.append(h)
                elif win32gui.IsWindowVisible(h):
                    rect = win32gui.GetWindowRect(h)
                    w = rect[2] - rect[0]
                    h_size = rect[3] - rect[1]
                    if w > 300 and h_size > 300:
                        found_windows.append(h)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(enum_cb, None)
    except Exception:
        pass
    if found_windows:
        return found_windows[0]
    return None

def get_thread_name_map():
    """Сопоставление thread_id -> имя чата из session_index.jsonl"""
    name_map = {}
    if not os.path.exists(SESSION_INDEX_FILE):
        return name_map
    try:
        with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as f:
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

def get_thread_project_map():
    """Сопоставление thread_id -> путь к проекту cwd из session_index.jsonl"""
    proj_map = {}
    if not os.path.exists(SESSION_INDEX_FILE):
        return proj_map
    try:
        with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as f:
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

def get_all_recent_goals():
    """Получение недавних целей из goals_1.sqlite для меню и мониторинга"""
    results = []
    if not os.path.exists(GOALS_DB):
        return results
    try:
        name_map = get_thread_name_map()
        conn = sqlite3.connect(f"file:{GOALS_DB}?mode=ro", uri=True, timeout=1.0)
        cursor = conn.cursor()
        one_day_ago = int((time.time() - 24 * 3600) * 1000)
        cursor.execute(
            "SELECT thread_id, status, updated_at_ms, objective FROM thread_goals WHERE updated_at_ms > ? ORDER BY updated_at_ms DESC LIMIT 10",
            (one_day_ago,)
        )
        goals = cursor.fetchall()
        conn.close()

        con_h = None
        if os.path.exists(HISTORY_DB):
            con_h = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True, timeout=1.0)

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
            results.append((c_name, g_st, turn_st, tid, upd_ms))

        if con_h:
            con_h.close()
    except Exception as e:
        logger.debug(f"Ошибка get_all_recent_goals: {e}")
    return results

def get_latest_turn_info(thread_id=None):
    """Получение информации о последнем шаге (turn) из thread_history_1.sqlite"""
    if not os.path.exists(HISTORY_DB):
        return None, None, None, None
    try:
        conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True, timeout=1.0)
        cursor = conn.cursor()
        if thread_id:
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

def is_thread_in_progress(thread_id):
    """Проверка, выполняется ли сейчас генерация в указанном потоке"""
    try:
        if not os.path.exists(HISTORY_DB):
            return False
        conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True, timeout=1.0)
        cur = conn.cursor()
        cur.execute("SELECT status FROM thread_turns WHERE thread_id = ? ORDER BY rowid DESC LIMIT 1", (thread_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0] == "inProgress":
            return True
    except Exception:
        pass
    return False

def get_goal_info_for_thread(thread_id=None):
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

    name_map = get_thread_name_map()
    if tid_str and tid_str in name_map:
        chat_name = name_map[tid_str]

    # 1. Проверяем thread_goals в goals_1.sqlite
    if os.path.exists(GOALS_DB):
        try:
            conn = sqlite3.connect(f"file:{GOALS_DB}?mode=ro", uri=True, timeout=1.0)
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
    if not goal_text and tid_str and os.path.exists(HISTORY_DB):
        try:
            conn = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True, timeout=1.0)
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

def invoke_button(btn_tuple):
    """Вызов действия кнопки через UIA чисто в памяти Chromium (без влияния на активные окна)"""
    if not btn_tuple:
        return False
    btn, label = btn_tuple
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
class WatchdogWorker(QObject):
    goals_updated = pyqtSignal(list)
    resume_triggered = pyqtSignal(str)
    stats_updated = pyqtSignal(int)
    state_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.is_enabled = True
        self.resumes_count = 0
        self.force_resume_flag = False
        self.consecutive_retries = 0
        self.last_codex_running = None
        self._running = True
        self._usage_wait_key = None
        self._usage_sent_key = None
        self._usage_next_try = 0.0

    def toggle(self):
        self.is_enabled = not self.is_enabled
        self.consecutive_retries = 0
        self.state_changed.emit(self.is_enabled)
        logger.info(f"Авто-возобновление переключено: {'ВКЛЮЧЕНО' if self.is_enabled else 'ВЫКЛЮЧЕНО (пауза)'}")
        return self.is_enabled

    def force_resume(self):
        self.force_resume_flag = True
        self.consecutive_retries = 0
        logger.info("Ручной вызов возобновления цели.")

    def stop(self):
        self._running = False

    def try_usage_limit_resume(self, hwnd, s, max_retries):
        """
        Лимит использования исчерпан: кнопок Resume/Retry нет, поэтому после времени сброса
        отправляем в открытый чат сообщение-продолжение. Возвращает True, если сообщение отправлено.
        """
        tid, turn_status, err_json, completed_at = get_latest_turn_info()
        if turn_status != "failed" or not is_usage_limit_error(err_json):
            return False
        if not settings_mgr.is_error_allowed(err_json):
            return False

        turn_key = (tid, completed_at)
        if turn_key == self._usage_sent_key:
            return False

        thread_cwd = get_thread_project_map().get(str(tid).strip()) if tid else None
        if thread_cwd and not settings_mgr.is_project_enabled(thread_cwd):
            return False

        reset_ts = parse_usage_limit_reset(err_json, completed_at)
        chat_name = get_thread_name_map().get(str(tid).strip()) if tid else None
        if turn_key != self._usage_wait_key:
            self._usage_wait_key = turn_key
            self._usage_next_try = 0.0
            if reset_ts:
                logger.info(
                    f"Лимит использования исчерпан | Чат: «{chat_name or 'Активный чат'}». "
                    f"Продолжение после сброса: {datetime.fromtimestamp(reset_ts):%d.%m %H:%M}."
                )
            else:
                logger.warning("Лимит использования исчерпан, но время сброса в ошибке не найдено. Авто-продолжение пропущено.")
        if not reset_ts:
            return False

        now = time.time()
        margin = float(s.get("usage_limit_resume_margin_seconds", 60.0))
        if now < reset_ts + margin or now < self._usage_next_try:
            return False
        if max_retries > 0 and self.consecutive_retries >= max_retries:
            return False

        # Сообщение уходит в открытый чат, поэтому он должен совпадать с прерванным
        visible = get_visible_chat_title(hwnd)
        if chat_name and visible and chat_name.strip().lower() != visible.strip().lower():
            # Пользователь не за компьютером -> возвращаемся в прерванный чат сами
            if get_user_idle_seconds() >= USER_IDLE_BEFORE_CHAT_SWITCH and switch_to_sidebar_chat(hwnd, chat_name):
                logger.info(f"Лимит сброшен, открыт другой чат («{visible}»). Переключение на «{chat_name}».")
                self._usage_next_try = now + 5.0
                return False
            logger.info(f"Лимит сброшен, но открыт другой чат («{visible}» вместо «{chat_name}»). Продолжение отложено.")
            self._usage_next_try = now + 60.0
            return False

        message = str(s.get("usage_limit_resume_message", "continue")).strip() or "continue"
        if not send_chat_message(hwnd, message):
            logger.warning("Лимит сброшен, но отправить сообщение не удалось (кнопка Send неактивна). Повтор через 60 сек.")
            self._usage_next_try = now + 60.0
            return False

        self._usage_sent_key = turn_key
        self.resumes_count += 1
        self.consecutive_retries += 1
        self.stats_updated.emit(self.resumes_count)
        self.resume_triggered.emit("ОТВЕТ")
        logger.info(
            f">>> ВОЗОБНОВЛЕНИЕ (ЛИМИТ) | Чат: «{chat_name or visible or 'Активный чат'}» | "
            f"Отправлено сообщение: «{message}»! Всего: {self.resumes_count} <<<"
        )
        return True

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
                retry_pause_cfg = float(s.get("retry_pause_seconds", 10.0))
                cooldown_cfg = float(s.get("post_resume_cooldown", 6.0))
                max_retries = int(s.get("max_retries_consecutive", 10))
                enable_on_codex_start = bool(s.get("enable_on_codex_start", True))

                goals = get_all_recent_goals()
                self.goals_updated.emit(goals)

                hwnd = find_codex_window()
                codex_is_running = (hwnd is not None)

                # Обработка настройки «Включать при старте Кодекса»
                if enable_on_codex_start:
                    if self.last_codex_running is False and codex_is_running is True:
                        if not self.is_enabled:
                            self.is_enabled = True
                            self.consecutive_retries = 0
                            self.state_changed.emit(True)
                            logger.info("Обнаружен запуск Кодекса -> авто-возобновление автоматически ВКЛЮЧЕНО.")
                self.last_codex_running = codex_is_running

                if not self.is_enabled or not hwnd:
                    time.sleep(poll_interval)
                    continue

                goal_btn, retry_btn, is_running = find_uia_action_buttons(hwnd)

                if is_running and not (goal_btn or retry_btn):
                    # Задача генерируется прямо сейчас
                    self.consecutive_retries = 0
                    time.sleep(poll_interval)
                    continue

                if goal_btn or retry_btn or self.force_resume_flag:
                    tid, turn_status, err_json, completed_at = get_latest_turn_info()
                    proj_map = get_thread_project_map()
                    thread_cwd = proj_map.get(str(tid).strip()) if tid else None

                    # 1. Проверка фильтра проектов
                    if thread_cwd and not settings_mgr.is_project_enabled(thread_cwd):
                        logger.info(f"Проект '{thread_cwd}' исключён в настройках. Авто-возобновление пропущено.")
                        time.sleep(poll_interval * 2)
                        continue

                    # 2. Проверка фильтра ошибок (авто-возобновление ТОЛЬКО при наличии отслеживаемой ошибки)
                    if not self.force_resume_flag:
                        if not err_json or turn_status in ("interrupted", "completed"):
                            # Ручная остановка пользователем или нормальное завершение -> НЕ возобновлять
                            time.sleep(poll_interval)
                            continue

                        if not (hasattr(settings_mgr, 'is_error_allowed') and settings_mgr.is_error_allowed(err_json)):
                            logger.info(f"Ошибка '{err_json[:60]}' отключена в настройках или не отслеживается. Авто-возобновление пропущено.")
                            time.sleep(poll_interval * 2)
                            continue

                        # До сброса лимита использования нажимать кнопку бессмысленно
                        if is_usage_limit_error(err_json):
                            reset_ts = parse_usage_limit_reset(err_json, completed_at)
                            margin = float(s.get("usage_limit_resume_margin_seconds", 60.0))
                            if reset_ts and time.time() < reset_ts + margin:
                                time.sleep(poll_interval * 2)
                                continue

                    # 3. Лимит повторов подряд
                    if max_retries > 0 and self.consecutive_retries >= max_retries:
                        logger.warning(f"Достигнут лимит повторов подряд ({max_retries}). Ожидание действий пользователя.")
                        time.sleep(poll_interval * 3)
                        continue

                    is_goal = (goal_btn is not None)
                    mode_name = "ЦЕЛЬ" if is_goal else "ОТВЕТ"
                    btn_chosen = goal_btn if goal_btn else retry_btn

                    now = time.time()
                    if self.force_resume_flag:
                        pause_left = 0.0
                        self.force_resume_flag = False
                    elif completed_at and (now - completed_at) < retry_pause_cfg:
                        elapsed = now - completed_at
                        pause_left = max(0.5, retry_pause_cfg - elapsed)
                    else:
                        pause_left = 1.0

                    if pause_left > 0:
                        btn_name_disp = btn_chosen[1] if btn_chosen else "ручной запуск"
                        logger.info(f"Обнаружена кнопка '{btn_name_disp}'. Ожидание паузы {pause_left:.1f} сек...")
                        wait_start = time.time()
                        while time.time() - wait_start < pause_left and self._running and self.is_enabled:
                            time.sleep(0.5)

                    if not self.is_enabled or not self._running:
                        continue

                    # Повторная проверка кнопок перед кликом
                    goal_btn, retry_btn, is_running = find_uia_action_buttons(hwnd)
                    if is_running:
                        time.sleep(poll_interval)
                        continue

                    target_btn = goal_btn if goal_btn else retry_btn
                    if target_btn:
                        btn_name = target_btn[1]
                        success = invoke_button(target_btn)
                        if success:
                            self.resumes_count += 1
                            self.consecutive_retries += 1
                            self.stats_updated.emit(self.resumes_count)
                            self.resume_triggered.emit(mode_name)

                            chat_name, goal_obj, is_explicit = get_goal_info_for_thread(tid)
                            goal_clean = " ".join(goal_obj.split()) if goal_obj else ""
                            goal_summary = goal_clean[:120] if goal_clean else ("Цель Codex" if is_goal else "Задача диалога")
                            item_type = "ЦЕЛЬ" if (is_explicit or is_goal) else "ЗАДАЧА"

                            logger.info(
                                f">>> ВОЗОБНОВЛЕНИЕ ({item_type}) | Чат: «{chat_name}» | {item_type.capitalize()}: «{goal_summary}» "
                                f"(кнопка: '{btn_name}')! Всего: {self.resumes_count} <<<"
                            )

                            # Звуковое оповещение (если включено в настройках)
                            if s.get("sound_on_resume", False):
                                try:
                                    ctypes.windll.user32.MessageBeep(0x00000040)
                                except Exception:
                                    pass

                            time.sleep(cooldown_cfg)
                            continue

                elif not is_running:
                    dialog_buttons = list(ALWAYS_DISMISS_DIALOG_BUTTONS)
                    if s.get("auto_trust_folders", False):
                        dialog_buttons.append("Trust folders")
                    if dismiss_blocking_dialog(hwnd, dialog_buttons):
                        time.sleep(poll_interval * 2)
                        continue
                    if self.try_usage_limit_resume(hwnd, s, max_retries):
                        time.sleep(cooldown_cfg)
                        continue

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

        # Скрытый виджет для привязки сообщений меню Win32
        self.dummy = QWidget()
        self.hwnd = int(self.dummy.winId())

        self.init_tray()

        # Сигналы
        self.worker.goals_updated.connect(self.on_goals_updated)
        self.worker.stats_updated.connect(self.on_stats_updated)
        self.worker.resume_triggered.connect(self.on_resume_triggered)
        self.worker.state_changed.connect(self.on_state_changed)

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
            "working": QColor(14, 165, 233)
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

        p.end()
        return QIcon(pix)

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.update_tray_visuals()
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def update_tray_visuals(self, state=None):
        cur_state = state or ("active" if self.worker.is_enabled else "paused")
        self.tray.setIcon(self.create_tray_icon(cur_state))

        tr = settings_mgr.tr
        st_str = tr("tip_enabled") if self.worker.is_enabled else tr("tip_disabled")
        tip = [st_str]
        if self.recent_goals:
            active_names = [g[0] for g in self.recent_goals if g[1] in ("active", "blocked") or g[2] == "inProgress"]
            if active_names:
                tip.append(tr("tip_goals", count=len(active_names), names=", ".join(active_names[:2])))
        tip.append(tr("tip_resumes", count=self.worker.resumes_count))
        self.tray.setToolTip("\n".join(tip))

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
                c_name, g_status, turn_st, tid, ts = g
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
            self.switch_chat(chosen_goal[0])

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

    def switch_chat(self, chat_name):
        hwnd = find_codex_window()
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
