# -*- coding: utf-8 -*-
"""
Codex Auto-Resume Settings GUI (Windows 11 Fluent Design)
Полноценный интерфейс на QFluentWidgets (Microsoft Fluent Design).
100% сплошной темный фон, аппаратное ускорение, отсутствие лагов перемещения,
полная мультиязычность (RU/EN), управление ошибками, проектами и таймингами.
"""

import os
import sys
import json
import re
import ctypes
from ctypes import wintypes
import logging

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

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mrpanica.codex.autoresume.v2")
except Exception:
    pass

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QFrame, QSizePolicy, QStackedWidget
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QFont

from qfluentwidgets import (
    NavigationBar, NavigationItemPosition, setTheme, Theme, FluentIcon as FIF,
    SettingCard, SettingCardGroup, SwitchSettingCard, CardWidget,
    BodyLabel, SubtitleLabel, CaptionLabel, StrongBodyLabel,
    SwitchButton, PrimaryPushButton, PushButton, ToolButton,
    SearchLineEdit, LineEdit, Slider, ComboBox, ScrollArea,
    InfoBar, InfoBarPosition, IconWidget, FluentStyleSheet,
    DoubleSpinBox, SpinBox
)

setTheme(Theme.DARK)

# -------------------------------------------------------------
# Пути к файлам и конфигурация
# -------------------------------------------------------------
USER_PROFILE = os.environ.get("USERPROFILE", r"C:\Users\Artur")
CODEX_DIR = os.path.join(USER_PROFILE, ".codex")
os.makedirs(CODEX_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(CODEX_DIR, "guardian_settings.json")
LOG_FILE = os.path.join(CODEX_DIR, "guardian.log")
SESSION_INDEX_FILE = os.path.join(CODEX_DIR, "session_index.jsonl")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FILE = os.path.join(APP_DIR, "icon.ico")
if not os.path.exists(ICON_FILE):
    ICON_FILE = os.path.join(CODEX_DIR, "icon.ico")

AUTOSTART_VBS = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup\CodexAutoResumeWatchdog.vbs"
)

logger = logging.getLogger("CodexTrayApp")

# -------------------------------------------------------------
# Дефолтная конфигурация
# -------------------------------------------------------------
DEFAULT_SETTINGS = {
    "language": "auto",  # "auto", "ru", "en"
    "autostart_windows": True,
    "enable_on_codex_start": True,
    "notify_on_toggle": True,
    "notify_on_resume": False,
    "sound_on_resume": False,
    "retry_pause_seconds": 10.0,
    "post_resume_cooldown": 6.0,
    "poll_interval_seconds": 1.0,
    "max_retries_consecutive": 10,
    "usage_limit_resume_message": "continue",
    "usage_limit_resume_margin_seconds": 60.0,
    "auto_trust_folders": False,
    "error_patterns": [
        {"pattern": "selected model is at capacity", "name": "Model Capacity / Server Overload", "enabled": True, "custom": False},
        {"pattern": "error running remote compact task", "name": "Remote Compact Task Error", "enabled": True, "custom": False},
        {"pattern": "stream disconnected", "name": "Stream Disconnected / Network Loss", "enabled": True, "custom": False},
        {"pattern": "connection lost", "name": "Connection Lost", "enabled": True, "custom": False},
        {"pattern": "rate limit", "name": "Rate Limit Exceeded", "enabled": True, "custom": False},
        {"pattern": "usage limit", "name": "Usage Limit Reached", "enabled": True, "custom": False},
        {"pattern": "timeout", "name": "Request Timeout", "enabled": True, "custom": False},
        {"pattern": "an unexpected error occurred", "name": "Unexpected Server Error", "enabled": True, "custom": False},
        {"pattern": "serveroverloaded", "name": "Server Overloaded Code", "enabled": True, "custom": False}
    ],
    "excluded_projects": []
}

# -------------------------------------------------------------
# Словарь локализации (RU / EN)
# -------------------------------------------------------------
TRANSLATIONS = {
    "ru": {
        # Меню в системном трее
        "menu_auto_resume_on": "🟢 Авто-возобновление: ВКЛЮЧЕНО",
        "menu_auto_resume_off": "⚪ Авто-возобновление: ВЫКЛЮЧЕНО",
        "menu_goals_submenu": "🎯 Текущие цели Codex",
        "menu_no_goals": "Нет активных целей",
        "menu_force_resume": "⚡ Возобновить текущую цель",
        "menu_settings": "⚙️ Настройки...",
        "menu_resumes_count": "📊 Возобновлений: {count}",
        "menu_open_log": "📄 Открыть журнал (лог)",
        "menu_open_folder": "📁 Открыть папку данных (.codex)",
        "menu_autostart_windows": "🚀 Запуск при старте Windows",
        "menu_exit": "❌ Выход",

        # Всплывающая подсказка в трее
        "tip_enabled": "Codex Auto-Resume: ВКЛЮЧЕНО 🟢",
        "tip_disabled": "Codex Auto-Resume: ВЫКЛЮЧЕНО ⚪",
        "tip_goals": "Цели ({count}): {names}",
        "tip_resumes": "Возобновлений: {count}",

        # Всплывающие уведомления Windows
        "notif_app_title": "Codex Auto-Resume",
        "notif_started": "Сторож фоновых задач запущен ({state})",
        "notif_state_on": "🟢 Авто-возобновление задач ВКЛЮЧЕНО",
        "notif_state_off": "⚪ Авто-возобновление задач ВЫКЛЮЧЕНО",
        "notif_force_resume": "⚡ Принудительно отправлен сигнал возобновления",
        "notif_autostart_on": "Автозапуск при старте Windows включен",
        "notif_autostart_off": "Автозапуск при старте Windows отключен",

        # Окно настроек
        "dialog_title": "Настройки Codex Auto-Resume",
        "nav_general": "Общие",
        "nav_errors": "Ошибки",
        "nav_projects": "Проекты",
        "nav_timers": "Тайминги",
        "nav_journal": "Журнал",

        "tab_general": "Общие параметры и запуск",
        "tab_errors": "Отслеживаемые типы ошибок",
        "tab_projects": "Участвующие проекты Codex",
        "tab_timers": "Параметры задержек и повторов",
        "tab_journal": "Журнал событий и статистика",

        "grp_language": "Язык интерфейса",
        "lang_desc": "Выберите язык приложения (применяется сразу)",
        "lang_auto": "Автоматически (Система)",
        "lang_auto_dynamic": "Автоматически ({lang})",
        "lang_ru_name": "Русский",
        "lang_en_name": "English",
        "lang_ru": "Русский (Russian)",
        "lang_en": "English (Английский)",

        "grp_startup": "Параметры запуска",
        "chk_autostart_windows": "Запуск при старте Windows",
        "desc_autostart_windows": "Автоматически запускать сторож в системном трее при входе в систему",
        "chk_enable_on_codex_start": "Включать при старте Codex",
        "desc_enable_on_codex_start": "Автоматически переводить сторож в активный режим при запуске приложения Codex",

        "grp_notifications": "Оповещения",
        "chk_notify_toggle": "Уведомление при переключении (Вкл / Выкл)",
        "desc_notify_toggle": "Показывать системное уведомление Windows при клике по ярлыку на рабочем столе или в меню",
        "chk_notify_resume": "Уведомление при возобновлении",
        "desc_notify_resume": "Показывать уведомление при каждом авто-возобновлении (может отвлекать во время игр)",
        "chk_sound_resume": "Звуковой сигнал при возобновлении",
        "desc_sound_resume": "Воспроизводить системный звук Windows при успешном возобновлении цели",

        "grp_error_patterns": "Список шаблонов ошибок",
        "desc_errors_info": "Сторож анализирует последние сбои в чатах Codex и возобновляет цель при совпадении с включёнными шаблонами.",
        "placeholder_new_error": "Введите фрагмент текста ошибки или код ошибки...",
        "btn_add_error": "Добавить",
        "btn_reset_errors": "Сбросить к стандартным",
        "badge_system": "Системная",
        "badge_custom": "Пользовательская",

        "err_model_capacity": "Перегрузка модели / сервера",
        "err_remote_compact": "Сбой задачи сжатия (Remote Compact)",
        "err_stream_disconnected": "Разрыв стрим-соединения / сети",
        "err_connection_lost": "Потеря сетевого соединения",
        "err_rate_limit": "Превышен лимит запросов (Rate Limit)",
        "err_timeout": "Таймаут ожидания ответа",
        "err_unexpected": "Неожиданная ошибка сервера",
        "err_server_overloaded": "Код перегрузки сервера (ServerOverloaded)",

        "grp_projects": "Список проектов Codex",
        "lbl_projects_desc": "Выберите проекты, для которых сторож будет автоматически возобновлять задачи. Неотмеченные проекты игнорируются.",
        "search_projects_placeholder": "Поиск проектов по имени или пути...",
        "btn_select_all": "Выбрать все",
        "btn_deselect_all": "Снять со всех",
        "projects_count_label": "Выбрано {selected} из {total} проектов",
        "no_projects_found": "Проекты Codex пока не найдены в session_index.jsonl",

        "grp_timers": "Тайминги и Лимиты",
        "lbl_retry_pause": "Пауза перед возобновлением",
        "desc_retry_pause": "Время ожидания перед нажатием кнопки возобновления после сбоя",
        "lbl_cooldown": "Кулдаун после возобновления",
        "desc_cooldown": "Задержка после возобновления перед следующим циклом проверки",
        "lbl_poll_interval": "Частота опроса (Интервал)",
        "desc_poll_interval": "Как часто сторож проверяет состояние генерации и окна Codex",
        "lbl_max_retries": "Максимум попыток подряд",
        "desc_max_retries": "Максимальное число повторов для одного сбоя (0 = без лимита)",

        "journal_stat_resumes": "Возобновлений целей",
        "journal_stat_errors": "Перехвачено сбоев",
        "journal_stat_status": "Статус сторожа",
        "journal_status_active": "Активен 🟢",
        "journal_status_paused": "На паузе ⚪",
        "journal_btn_refresh": "Обновить",
        "journal_btn_open_log": "Открыть лог",
        "journal_btn_clear": "Очистить журнал",
        "journal_empty": "Журнал пока пуст. События появятся при работе сторожа.",
        "journal_event_resume": "Возобновление",
        "journal_event_resume_goal": "Возобновление цели",
        "journal_event_resume_task": "Возобновление задачи",
        "journal_prefix_chat": "Чат",
        "journal_prefix_goal": "Цель",
        "journal_prefix_task": "Задача",
        "journal_prefix_btn": "кнопка",
        "journal_prefix_total": "всего",
        "journal_event_trigger": "Кнопка обнаружена",
        "journal_event_switch": "Смена чата",
        "journal_event_state": "Смена режима",
        "journal_event_start": "Запуск приложения",
        "journal_event_error": "Ошибка",
        "journal_search_placeholder": "Поиск по записям журнала...",

        "lbl_pattern_prefix": "Шаблон",
        "switch_on": "Вкл",
        "switch_off": "Выкл",
        "unit_seconds": "сек.",
        "unit_times": "раз",
        "unlimited": "Без ограничений",

        "btn_save": "Сохранить настройки",
        "msg_saved_title": "Настройки сохранены",
        "msg_saved_desc": "Новые параметры успешно сохранены и вступили в силу."
    },
    "en": {
        # Tray context menu
        "menu_auto_resume_on": "🟢 Auto-Resume: ENABLED",
        "menu_auto_resume_off": "⚪ Auto-Resume: DISABLED",
        "menu_goals_submenu": "🎯 Current Codex Goals",
        "menu_no_goals": "No active goals",
        "menu_force_resume": "⚡ Resume Current Goal",
        "menu_settings": "⚙️ Settings...",
        "menu_resumes_count": "📊 Resumes count: {count}",
        "menu_open_log": "📄 Open Log File",
        "menu_open_folder": "📁 Open Data Folder (.codex)",
        "menu_autostart_windows": "🚀 Start with Windows",
        "menu_exit": "❌ Exit",

        # Tray tooltip
        "tip_enabled": "Codex Auto-Resume: ENABLED 🟢",
        "tip_disabled": "Codex Auto-Resume: DISABLED ⚪",
        "tip_goals": "Goals ({count}): {names}",
        "tip_resumes": "Resumes: {count}",

        # Windows notifications
        "notif_app_title": "Codex Auto-Resume",
        "notif_started": "Background task guardian started ({state})",
        "notif_state_on": "🟢 Task auto-resume is ENABLED",
        "notif_state_off": "⚪ Task auto-resume is DISABLED",
        "notif_force_resume": "⚡ Force resume signal sent",
        "notif_autostart_on": "Start with Windows enabled",
        "notif_autostart_off": "Start with Windows disabled",

        # Settings dialog
        "dialog_title": "Codex Auto-Resume Settings",
        "nav_general": "General",
        "nav_errors": "Errors",
        "nav_projects": "Projects",
        "nav_timers": "Timers",
        "nav_journal": "Journal",

        "tab_general": "General & Startup Options",
        "tab_errors": "Monitored Error Patterns",
        "tab_projects": "Participating Codex Projects",
        "tab_timers": "Delay Timings & Limits",
        "tab_journal": "Event Journal & Statistics",

        "grp_language": "Interface Language",
        "lang_desc": "Choose application language (applied immediately)",
        "lang_auto": "Automatic (System Default)",
        "lang_auto_dynamic": "Automatic ({lang})",
        "lang_ru_name": "Russian",
        "lang_en_name": "English",
        "lang_ru": "Russian (Русский)",
        "lang_en": "English",

        "grp_startup": "Startup Options",
        "chk_autostart_windows": "Start on Windows Boot",
        "desc_autostart_windows": "Automatically launch the guardian in the system tray on Windows login",
        "chk_enable_on_codex_start": "Enable on Codex Start",
        "desc_enable_on_codex_start": "Automatically set guardian to active state when Codex application opens",

        "grp_notifications": "Notifications",
        "chk_notify_toggle": "Notification on Toggle (On / Off)",
        "desc_notify_toggle": "Show a Windows desktop notification when toggled via desktop shortcut or menu",
        "chk_notify_resume": "Notification on Resume",
        "desc_notify_resume": "Show a notification every time a goal is resumed (may disturb during gaming)",
        "chk_sound_resume": "Sound Alert on Resume",
        "desc_sound_resume": "Play a Windows system chime when a goal is successfully resumed",

        "grp_error_patterns": "Error Patterns List",
        "desc_errors_info": "The guardian inspects recent chat failures and automatically resumes goals matching enabled patterns.",
        "placeholder_new_error": "Enter error text snippet or error code...",
        "btn_add_error": "Add Pattern",
        "btn_reset_errors": "Reset to Defaults",
        "badge_system": "System",
        "badge_custom": "Custom",

        "err_model_capacity": "Model Capacity / Server Overload",
        "err_remote_compact": "Remote Compact Task Error",
        "err_stream_disconnected": "Stream Disconnected / Network Loss",
        "err_connection_lost": "Connection Lost",
        "err_rate_limit": "Rate Limit Exceeded",
        "err_timeout": "Request Timeout",
        "err_unexpected": "Unexpected Server Error",
        "err_server_overloaded": "Server Overloaded Code",

        "grp_projects": "Codex Projects List",
        "lbl_projects_desc": "Select projects for which auto-resume will operate. Unchecked projects will be ignored.",
        "search_projects_placeholder": "Search projects by name or path...",
        "btn_select_all": "Select All",
        "btn_deselect_all": "Deselect All",
        "projects_count_label": "Selected {selected} of {total} projects",
        "no_projects_found": "No Codex projects found in session_index.jsonl yet",

        "grp_timers": "Timers & Limits",
        "lbl_retry_pause": "Delay Before Resuming",
        "desc_retry_pause": "Cooldown duration before invoking the resume action after a failure",
        "lbl_cooldown": "Post-Resume Cooldown",
        "desc_cooldown": "Delay after resuming before resuming active polling checks",
        "lbl_poll_interval": "Polling Frequency (Interval)",
        "desc_poll_interval": "How frequently the watchdog polls Codex generation state and windows",
        "lbl_max_retries": "Max Consecutive Retries",
        "desc_max_retries": "Maximum resume attempts for a single recurring failure (0 = unlimited)",

        "journal_stat_resumes": "Goals Resumed",
        "journal_stat_errors": "Intercepted Failures",
        "journal_stat_status": "Guardian Status",
        "journal_status_active": "Active 🟢",
        "journal_status_paused": "Paused ⚪",
        "journal_btn_refresh": "Refresh",
        "journal_btn_open_log": "Open Log File",
        "journal_btn_clear": "Clear Journal",
        "journal_empty": "Journal is empty yet. Events will appear as guardian runs.",
        "journal_event_resume": "Resumed",
        "journal_event_resume_goal": "Goal Resumed",
        "journal_event_resume_task": "Task Resumed",
        "journal_prefix_chat": "Chat",
        "journal_prefix_goal": "Goal",
        "journal_prefix_task": "Task",
        "journal_prefix_btn": "button",
        "journal_prefix_total": "total",
        "journal_event_trigger": "Button Detected",
        "journal_event_switch": "Chat Switch",
        "journal_event_state": "Status Change",
        "journal_event_start": "Guardian Started",
        "journal_event_error": "Error Notice",
        "journal_search_placeholder": "Search journal logs...",

        "lbl_pattern_prefix": "Pattern",
        "switch_on": "On",
        "switch_off": "Off",
        "unit_seconds": "sec",
        "unit_times": "times",
        "unlimited": "Unlimited",

        "btn_save": "Save Settings",
        "msg_saved_title": "Settings Saved",
        "msg_saved_desc": "New settings have been saved and applied successfully."
    }
}

def detect_system_language():
    try:
        kernel32 = ctypes.windll.kernel32
        lang_id = kernel32.GetUserDefaultUILanguage() & 0xFF
        if lang_id == 0x19:  # Russian
            return "ru"
    except Exception:
        pass
    return "en"

# -------------------------------------------------------------
# Менеджер настроек
# -------------------------------------------------------------
class SettingsManager:
    def __init__(self):
        self.settings = dict(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    self.settings[k] = v
            except Exception:
                pass
        else:
            self.save()

    def save(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def get_effective_language(self):
        lang = self.settings.get("language", "auto")
        if lang == "auto":
            return detect_system_language()
        return lang if lang in TRANSLATIONS else "ru"

    def tr(self, key, **kwargs):
        lang = self.get_effective_language()
        msg = TRANSLATIONS.get(lang, TRANSLATIONS["ru"]).get(key, key)
        if kwargs:
            try:
                msg = msg.format(**kwargs)
            except Exception:
                pass
        return msg

    def get_error_patterns(self):
        patterns = self.settings.get("error_patterns", [])
        return [p["pattern"].lower() for p in patterns if p.get("enabled", True)]

    def is_project_enabled(self, project_path):
        if not project_path:
            return True
        excluded = self.settings.get("excluded_projects", [])
        norm_proj = os.path.normpath(project_path).lower()
        for exc in excluded:
            if os.path.normpath(exc).lower() == norm_proj:
                return False
        return True

    def is_error_allowed(self, err_text):
        if not err_text:
            return False
        err_lower = str(err_text).lower()
        patterns = self.settings.get("error_patterns", [])
        active_patterns = [p for p in patterns if p.get("enabled", True)]
        if not active_patterns:
            return False
        for p in active_patterns:
            pat_str = p.get("pattern", "").strip().lower()
            if pat_str and pat_str in err_lower:
                return True
        return False

settings_mgr = SettingsManager()

def get_all_codex_projects():
    """Сбор всех уникальных проектов из session_index.jsonl"""
    projects = {}
    if os.path.exists(SESSION_INDEX_FILE):
        try:
            with open(SESSION_INDEX_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        cwd = obj.get("cwd")
                        if cwd:
                            norm = os.path.normpath(cwd).replace("\\\\?\\", "")
                            base = os.path.basename(norm) or norm
                            if norm not in projects:
                                projects[norm] = base
                    except Exception:
                        pass
        except Exception:
            pass
    return projects

def attach_desktop():
    """Безопасно переключает поток на рабочий стол Default (интерактивный рабочий стол Windows)."""
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

# -------------------------------------------------------------
# Пользовательские карточки Fluent Design (на базе SettingCard)
# -------------------------------------------------------------
class CustomSliderCard(SettingCard):
    """Карточка со слайдером на базе SettingCard"""
    valueChanged = pyqtSignal(int)

    def __init__(self, icon, title, content, min_val, max_val, cur_val, unit="сек.", parent=None):
        super().__init__(icon, title, content, parent)
        self.unit = unit
        self.min_val = min_val

        self.val_lbl = QLabel(f"{cur_val} {unit}", self)
        self.val_lbl.setStyleSheet("color: #70d6ff; font-size: 13px; font-weight: bold;")
        self.val_lbl.setFixedWidth(75)
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(min_val, max_val)
        self.slider.setValue(cur_val)
        self.slider.setFixedWidth(180)
        self.slider.valueChanged.connect(self._on_slider_changed)

        self.hBoxLayout.addWidget(self.val_lbl)
        self.hBoxLayout.addSpacing(10)
        self.hBoxLayout.addWidget(self.slider)
        self.hBoxLayout.addSpacing(16)
        self._on_slider_changed(cur_val)

    def _on_slider_changed(self, val):
        if self.min_val == 0 and val == 0:
            self.val_lbl.setText(settings_mgr.tr("unlimited"))
        else:
            self.val_lbl.setText(f"{val} {self.unit}")
        self.valueChanged.emit(val)

    def value(self):
        return self.slider.value()

    def setValue(self, val):
        self.slider.setValue(val)

    def update_texts(self, title, content, unit):
        self.unit = unit
        self.titleLabel.setText(title)
        self.contentLabel.setText(content)
class CustomDoubleSpinCard(SettingCard):
    """Карточка для точного числового ввода с плавающей точкой (DoubleSpinBox)"""
    valueChanged = pyqtSignal(float)

    def __init__(self, icon, title, content, min_val, max_val, cur_val, step=0.5, decimals=1, unit="сек.", parent=None):
        super().__init__(icon, title, content, parent)
        self.spin = DoubleSpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        self.spin.setValue(float(cur_val))
        self.spin.setFixedWidth(130)

        self.unit_lbl = QLabel(unit, self)
        self.unit_lbl.setStyleSheet("color: #70d6ff; font-size: 13px; font-weight: bold;")
        self.unit_lbl.setFixedWidth(50)

        self.hBoxLayout.addWidget(self.spin)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(self.unit_lbl)
        self.hBoxLayout.addSpacing(16)
        self.spin.valueChanged.connect(lambda v: self.valueChanged.emit(float(v)))

    def value(self):
        return self.spin.value()

    def setValue(self, val):
        self.spin.blockSignals(True)
        self.spin.setValue(float(val))
        self.spin.blockSignals(False)

    def update_texts(self, title, content, unit):
        self.setTitle(title)
        self.setContent(content)
        self.unit_lbl.setText(unit)


class CustomSpinCard(SettingCard):
    """Карточка для точного целочисленного ввода (SpinBox)"""
    valueChanged = pyqtSignal(int)

    def __init__(self, icon, title, content, min_val, max_val, cur_val, step=1, unit="раз", parent=None):
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setValue(int(cur_val))
        self.spin.setFixedWidth(130)

        self.unit_lbl = QLabel(unit, self)
        self.unit_lbl.setStyleSheet("color: #70d6ff; font-size: 13px; font-weight: bold;")
        self.unit_lbl.setFixedWidth(50)

        self.hBoxLayout.addWidget(self.spin)
        self.hBoxLayout.addSpacing(8)
        self.hBoxLayout.addWidget(self.unit_lbl)
        self.hBoxLayout.addSpacing(16)
        self.spin.valueChanged.connect(lambda v: self.valueChanged.emit(int(v)))

    def value(self):
        return self.spin.value()

    def setValue(self, val):
        self.spin.blockSignals(True)
        self.spin.setValue(int(val))
        self.spin.blockSignals(False)

    def update_texts(self, title, content, unit):
        self.setTitle(title)
        self.setContent(content)
        self.unit_lbl.setText(unit)


class CustomComboCard(SettingCard):
    """Карточка с выпадающим списком ComboBox на базе SettingCard"""
    currentIndexChanged = pyqtSignal(int)

    def __init__(self, icon, title, content, items, cur_idx=0, parent=None):
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        self.combo.addItems(items)
        self.combo.setCurrentIndex(cur_idx)
        self.combo.setFixedWidth(210)
        self.combo.currentIndexChanged.connect(self.currentIndexChanged.emit)

        self.hBoxLayout.addWidget(self.combo)
        self.hBoxLayout.addSpacing(16)

    def currentIndex(self):
        return self.combo.currentIndex()

    def setCurrentIndex(self, idx):
        self.combo.setCurrentIndex(idx)

    def update_texts(self, title, content, items=None):
        self.titleLabel.setText(title)
        self.contentLabel.setText(content)
        if items:
            cur = self.combo.currentIndex()
            self.combo.clear()
            self.combo.addItems(items)
            if 0 <= cur < len(items):
                self.combo.setCurrentIndex(cur)


class CustomSwitchSettingCard(SwitchSettingCard):
    """SwitchSettingCard с поддержкой мультиязычных подписей Вкл / Выкл (On / Off)"""
    def __init__(self, icon, title, content=None, parent=None):
        super().__init__(icon, title, content, parent=parent)
        self.update_switch_texts()

    def update_switch_texts(self):
        tr = settings_mgr.tr
        on_txt = tr("switch_on")
        off_txt = tr("switch_off")
        self.switchButton.setOnText(on_txt)
        self.switchButton.setOffText(off_txt)
        self.switchButton.setText(on_txt if self.isChecked() else off_txt)

    def setValue(self, isChecked: bool):
        self.switchButton.setChecked(isChecked)
        tr = settings_mgr.tr
        self.switchButton.setText(tr("switch_on") if isChecked else tr("switch_off"))


STANDARD_ERROR_MAP = {
    "selected model is at capacity": "err_model_capacity",
    "error running remote compact task": "err_remote_compact",
    "stream disconnected": "err_stream_disconnected",
    "connection lost": "err_connection_lost",
    "rate limit": "err_rate_limit",
    "rate limit exceeded": "err_rate_limit",
    "timeout": "err_timeout",
    "timed out waiting for response": "err_timeout",
    "an unexpected error occurred": "err_unexpected",
    "serveroverloaded": "err_server_overloaded",
}

# -------------------------------------------------------------
# Главное окно настроек на QFluentWidgets (QMainWindow)
# -------------------------------------------------------------
class SettingsWindow(QMainWindow):
    settings_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mgr = settings_mgr
        self.mgr.load()

        # Инициализация геометрии и окна
        self.setWindowTitle(self.mgr.tr("dialog_title"))
        self.resize(960, 720)
        self.setMinimumSize(880, 620)

        # Нативная темная рамка Windows 11 (DWMWA_USE_IMMERSIVE_DARK_MODE)
        try:
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            hwnd = int(self.winId())
            val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(val), ctypes.sizeof(val)
            )
        except Exception:
            pass

        if os.path.exists(ICON_FILE):
            self.setWindowIcon(QIcon(ICON_FILE))

        self.setStyleSheet("""
            QMainWindow, QWidget#centralWidget {
                background-color: #1a1b26;
            }
            ScrollArea, .ScrollArea {
                background-color: transparent;
                border: none;
            }
            CardWidget {
                background-color: #24283b;
                border: 1px solid #2f354d;
                border-radius: 8px;
            }
            CardWidget:hover {
                background-color: #292e42;
                border: 1px solid #414868;
            }
        """)

        central = QWidget(self)
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        h_layout = QHBoxLayout(central)
        h_layout.setContentsMargins(0, 0, 0, 0)
        h_layout.setSpacing(0)

        self.nav = NavigationBar(central)
        self.stack = QStackedWidget(central)
        h_layout.addWidget(self.nav)
        h_layout.addWidget(self.stack, 1)

        # Построение вкладок интерфейса
        self.init_interfaces()
        self.init_navigation()
        self.init_connections()
        self.load_values()

    def addSubInterface(self, interface: QWidget, icon, text: str):
        self.stack.addWidget(interface)
        route_key = interface.objectName() or str(id(interface))
        self.nav.addItem(
            route_key,
            icon,
            text,
            onClick=lambda: self.stack.setCurrentWidget(interface)
        )

    def get_auto_language_label(self):
        sys_lang = detect_system_language()
        tr = self.mgr.tr
        lang_name = tr("lang_ru_name") if sys_lang == "ru" else tr("lang_en_name")
        return tr("lang_auto_dynamic", lang=lang_name)

    def init_interfaces(self):
        self.interface_general = self.create_general_interface()
        self.interface_errors = self.create_errors_interface()
        self.interface_projects = self.create_projects_interface()
        self.interface_timers = self.create_timers_interface()
        self.interface_journal = self.create_journal_interface()

    def init_navigation(self):
        tr = self.mgr.tr
        self.addSubInterface(self.interface_general, FIF.SETTING, tr("nav_general"))
        self.addSubInterface(self.interface_errors, FIF.INFO, tr("nav_errors"))
        self.addSubInterface(self.interface_projects, FIF.FOLDER, tr("nav_projects"))
        self.addSubInterface(self.interface_timers, FIF.SPEED_HIGH, tr("nav_timers"))
        self.addSubInterface(self.interface_journal, FIF.DOCUMENT, tr("nav_journal"))
        self.nav.setCurrentItem(self.interface_general.objectName())
        self.stack.setCurrentWidget(self.interface_general)

    def init_connections(self):
        # Привязка переключателей к авто-сохранению единожды
        self.card_autostart.checkedChanged.connect(self._sync_autostart)
        self.card_codex_start.checkedChanged.connect(lambda c: self._quick_save("enable_on_codex_start", c))
        self.card_notif_toggle.checkedChanged.connect(lambda c: self._quick_save("notify_on_toggle", c))
        self.card_notif_resume.checkedChanged.connect(lambda c: self._quick_save("notify_on_resume", c))
        self.card_sound_resume.checkedChanged.connect(lambda c: self._quick_save("sound_on_resume", c))

    # ---------------------------------------------------------
    # Вкладка 1: Общие и Запуск
    # ---------------------------------------------------------
    def create_general_interface(self):
        scroll = ScrollArea()
        scroll.setObjectName("generalInterface")
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(36, 24, 36, 24)
        vbox.setSpacing(22)

        # Заголовок страницы
        self.lbl_title_gen = SubtitleLabel(self.mgr.tr("tab_general"), container)
        vbox.addWidget(self.lbl_title_gen)

        # Группа 1: Язык
        self.grp_lang = SettingCardGroup(self.mgr.tr("grp_language"), container)
        lang_items = [self.get_auto_language_label(), self.mgr.tr("lang_ru"), self.mgr.tr("lang_en")]
        self.card_lang = CustomComboCard(
            FIF.LANGUAGE,
            self.mgr.tr("grp_language"),
            self.mgr.tr("lang_desc"),
            lang_items,
            cur_idx=0,
            parent=self.grp_lang
        )
        self.card_lang.currentIndexChanged.connect(self.on_language_changed)
        self.grp_lang.addSettingCard(self.card_lang)
        vbox.addWidget(self.grp_lang)

        # Группа 2: Параметры запуска
        self.grp_startup = SettingCardGroup(self.mgr.tr("grp_startup"), container)
        self.card_autostart = CustomSwitchSettingCard(
            FIF.POWER_BUTTON,
            self.mgr.tr("chk_autostart_windows"),
            self.mgr.tr("desc_autostart_windows"),
            parent=self.grp_startup
        )
        self.card_codex_start = CustomSwitchSettingCard(
            FIF.PLAY,
            self.mgr.tr("chk_enable_on_codex_start"),
            self.mgr.tr("desc_enable_on_codex_start"),
            parent=self.grp_startup
        )
        self.grp_startup.addSettingCard(self.card_autostart)
        self.grp_startup.addSettingCard(self.card_codex_start)
        vbox.addWidget(self.grp_startup)

        # Группа 3: Оповещения
        self.grp_notif = SettingCardGroup(self.mgr.tr("grp_notifications"), container)
        self.card_notif_toggle = CustomSwitchSettingCard(
            FIF.CHAT,
            self.mgr.tr("chk_notify_toggle"),
            self.mgr.tr("desc_notify_toggle"),
            parent=self.grp_notif
        )
        self.card_notif_resume = CustomSwitchSettingCard(
            FIF.RINGER,
            self.mgr.tr("chk_notify_resume"),
            self.mgr.tr("desc_notify_resume"),
            parent=self.grp_notif
        )
        self.card_sound_resume = CustomSwitchSettingCard(
            FIF.VOLUME,
            self.mgr.tr("chk_sound_resume"),
            self.mgr.tr("desc_sound_resume"),
            parent=self.grp_notif
        )
        self.grp_notif.addSettingCard(self.card_notif_toggle)
        self.grp_notif.addSettingCard(self.card_notif_resume)
        self.grp_notif.addSettingCard(self.card_sound_resume)
        vbox.addWidget(self.grp_notif)

        vbox.addStretch(1)
        scroll.setWidget(container)
        return scroll

    # ---------------------------------------------------------
    # Вкладка 2: Ошибки
    # ---------------------------------------------------------
    def create_errors_interface(self):
        scroll = ScrollArea()
        scroll.setObjectName("errorsInterface")
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(36, 24, 36, 24)
        vbox.setSpacing(18)

        # Заголовок страницы
        self.lbl_title_err = SubtitleLabel(self.mgr.tr("tab_errors"), container)
        vbox.addWidget(self.lbl_title_err)

        self.lbl_desc_err = CaptionLabel(self.mgr.tr("desc_errors_info"), container)
        self.lbl_desc_err.setTextColor(QColor("#a6adc8"), QColor("#a6adc8"))
        vbox.addWidget(self.lbl_desc_err)

        # Карточка добавления новой ошибки
        add_card = CardWidget(container)
        add_layout = QHBoxLayout(add_card)
        add_layout.setContentsMargins(18, 14, 18, 14)
        add_layout.setSpacing(12)

        self.edit_new_error = LineEdit(add_card)
        self.edit_new_error.setPlaceholderText(self.mgr.tr("placeholder_new_error"))
        self.edit_new_error.returnPressed.connect(self.on_add_error_clicked)
        add_layout.addWidget(self.edit_new_error, 1)

        self.btn_add_error = PrimaryPushButton(FIF.ADD, self.mgr.tr("btn_add_error"), add_card)
        self.btn_add_error.clicked.connect(self.on_add_error_clicked)
        add_layout.addWidget(self.btn_add_error)

        self.btn_reset_errors = PushButton(FIF.SYNC, self.mgr.tr("btn_reset_errors"), add_card)
        self.btn_reset_errors.clicked.connect(self.on_reset_errors_clicked)
        add_layout.addWidget(self.btn_reset_errors)

        vbox.addWidget(add_card)

        # Контейнер списка шаблонов ошибок
        self.grp_errors = SettingCardGroup(self.mgr.tr("grp_error_patterns"), container)
        vbox.addWidget(self.grp_errors)

        self.error_item_widgets = []

        vbox.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def refresh_error_cards(self):
        while self.grp_errors.cardLayout.count():
            item = self.grp_errors.cardLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.error_item_widgets.clear()

        patterns = self.mgr.settings.get("error_patterns", [])
        tr = self.mgr.tr

        for item_data in patterns:
            pat = item_data.get("pattern", "")
            is_custom = item_data.get("custom", False)
            enabled = item_data.get("enabled", True)

            if not is_custom and pat.lower() in STANDARD_ERROR_MAP:
                name = tr(STANDARD_ERROR_MAP[pat.lower()])
            else:
                name = item_data.get("name") or pat

            card = SettingCard(FIF.INFO, name, f"{tr('lbl_pattern_prefix')}: {pat}", parent=self.grp_errors)

            # Бейдж (Системная / Пользовательская)
            badge_lbl = QLabel(tr("badge_custom") if is_custom else tr("badge_system"))
            badge_color = "#e0af68" if is_custom else "#70d6ff"
            badge_bg = "rgba(224, 175, 104, 0.15)" if is_custom else "rgba(112, 214, 255, 0.15)"
            badge_lbl.setStyleSheet(f"""
                QLabel {{
                    color: {badge_color};
                    background-color: {badge_bg};
                    border: 1px solid {badge_color};
                    border-radius: 4px;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: bold;
                }}
            """)
            badge_lbl.setFixedWidth(115)
            badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            card.hBoxLayout.insertWidget(0, badge_lbl)
            card.hBoxLayout.insertSpacing(1, 14)

            # Тумблер Вкл / Выкл
            sw = SwitchButton(card)
            sw.setOnText(tr("switch_on"))
            sw.setOffText(tr("switch_off"))
            sw.setChecked(enabled)
            sw.checkedChanged.connect(lambda checked, d=item_data: self._on_error_toggled(d, checked))
            card.hBoxLayout.addWidget(sw)

            # Кнопка удаления (только для пользовательских)
            if is_custom:
                card.hBoxLayout.addSpacing(10)
                del_btn = ToolButton(FIF.DELETE, card)
                del_btn.setToolTip("Удалить" if tr("switch_on") == "Вкл" else "Delete")
                del_btn.clicked.connect(lambda _, d=item_data: self._on_delete_error(d))
                card.hBoxLayout.addWidget(del_btn)

            card.hBoxLayout.addSpacing(16)

            self.grp_errors.addSettingCard(card)
            self.error_item_widgets.append((card, item_data, sw))

    def _on_error_toggled(self, item_data, checked):
        item_data["enabled"] = checked
        self.mgr.save()

    def _on_delete_error(self, item_data):
        patterns = self.mgr.settings.get("error_patterns", [])
        if item_data in patterns:
            patterns.remove(item_data)
            self.mgr.save()
            self.refresh_error_cards()

    def on_add_error_clicked(self):
        text = self.edit_new_error.text().strip()
        if not text:
            return
        patterns = self.mgr.settings.setdefault("error_patterns", [])
        if any(p.get("pattern", "").lower() == text.lower() for p in patterns):
            return
        patterns.append({
            "pattern": text.lower(),
            "name": text,
            "enabled": True,
            "custom": True
        })
        self.mgr.save()
        self.edit_new_error.clear()
        self.refresh_error_cards()

    def on_reset_errors_clicked(self):
        self.mgr.settings["error_patterns"] = json.loads(json.dumps(DEFAULT_SETTINGS["error_patterns"]))
        self.mgr.save()
        self.refresh_error_cards()

    # ---------------------------------------------------------
    # Вкладка 3: Проекты
    # ---------------------------------------------------------
    def create_projects_interface(self):
        scroll = ScrollArea()
        scroll.setObjectName("projectsInterface")
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(36, 24, 36, 24)
        vbox.setSpacing(18)

        # Заголовок страницы
        self.lbl_title_proj = SubtitleLabel(self.mgr.tr("tab_projects"), container)
        vbox.addWidget(self.lbl_title_proj)

        self.lbl_desc_proj = CaptionLabel(self.mgr.tr("lbl_projects_desc"), container)
        self.lbl_desc_proj.setTextColor(QColor("#a6adc8"), QColor("#a6adc8"))
        vbox.addWidget(self.lbl_desc_proj)

        # Панель действий: Поиск + кнопки Выбрать все / Снять все
        action_card = CardWidget(container)
        action_layout = QHBoxLayout(action_card)
        action_layout.setContentsMargins(18, 14, 18, 14)
        action_layout.setSpacing(12)

        self.search_projects = SearchLineEdit(action_card)
        self.search_projects.setPlaceholderText(self.mgr.tr("search_projects_placeholder"))
        self.search_projects.textChanged.connect(self.filter_project_cards)
        action_layout.addWidget(self.search_projects, 1)

        self.btn_select_all_proj = PushButton(FIF.CHECKBOX, self.mgr.tr("btn_select_all"), action_card)
        self.btn_select_all_proj.clicked.connect(self.on_select_all_projects)
        action_layout.addWidget(self.btn_select_all_proj)

        self.btn_deselect_all_proj = PushButton(FIF.CLOSE, self.mgr.tr("btn_deselect_all"), action_card)
        self.btn_deselect_all_proj.clicked.connect(self.on_deselect_all_projects)
        action_layout.addWidget(self.btn_deselect_all_proj)

        vbox.addWidget(action_card)

        # Статистика проектов
        self.lbl_proj_count = CaptionLabel("", container)
        self.lbl_proj_count.setTextColor(QColor("#70d6ff"), QColor("#70d6ff"))
        vbox.addWidget(self.lbl_proj_count)

        # Список проектов
        self.grp_projects = SettingCardGroup(self.mgr.tr("grp_projects"), container)
        vbox.addWidget(self.grp_projects)

        self.project_item_widgets = []

        vbox.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def refresh_project_cards(self):
        while self.grp_projects.cardLayout.count():
            item = self.grp_projects.cardLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.project_item_widgets.clear()

        all_projs = get_all_codex_projects()
        excluded = [str(x).strip().lower() for x in self.mgr.settings.get("excluded_projects", [])]

        if not all_projs:
            empty_card = SettingCard(FIF.INFO, self.mgr.tr("no_projects_found"), "", parent=self.grp_projects)
            self.grp_projects.addSettingCard(empty_card)
            self.lbl_proj_count.setText("")
            return

        selected_count = 0
        for p_path, p_base in sorted(all_projs.items(), key=lambda x: x[1].lower()):
            is_enabled = (p_path.strip().lower() not in excluded and p_base.strip().lower() not in excluded)
            if is_enabled:
                selected_count += 1

            card = SettingCard(FIF.FOLDER, p_base, p_path, parent=self.grp_projects)

            sw = SwitchButton(card)
            sw.setOnText(self.mgr.tr("switch_on"))
            sw.setOffText(self.mgr.tr("switch_off"))
            sw.setChecked(is_enabled)
            sw.checkedChanged.connect(lambda checked, p=p_path: self._on_project_toggled(p, checked))
            card.hBoxLayout.addWidget(sw)
            card.hBoxLayout.addSpacing(16)

            self.grp_projects.addSettingCard(card)
            self.project_item_widgets.append((card, p_path, p_base, sw))

        self.update_projects_count_label()

    def update_projects_count_label(self):
        total = len(self.project_item_widgets)
        selected = sum(1 for _, _, _, sw in self.project_item_widgets if sw.isChecked())
        self.lbl_proj_count.setText(self.mgr.tr("projects_count_label", selected=selected, total=total))

    def _on_project_toggled(self, p_path, checked):
        excluded = self.mgr.settings.setdefault("excluded_projects", [])
        norm = os.path.normpath(p_path).lower()
        if checked:
            self.mgr.settings["excluded_projects"] = [x for x in excluded if os.path.normpath(x).lower() != norm]
        else:
            if not any(os.path.normpath(x).lower() == norm for x in excluded):
                excluded.append(p_path)
        self.mgr.save()
        self.update_projects_count_label()

    def on_select_all_projects(self):
        self.mgr.settings["excluded_projects"] = []
        for _, _, _, sw in self.project_item_widgets:
            sw.setChecked(True)
        self.mgr.save()
        self.update_projects_count_label()

    def on_deselect_all_projects(self):
        all_paths = [p for _, p, _, _ in self.project_item_widgets]
        self.mgr.settings["excluded_projects"] = all_paths
        for _, _, _, sw in self.project_item_widgets:
            sw.setChecked(False)
        self.mgr.save()
        self.update_projects_count_label()

    def filter_project_cards(self, query):
        q = query.strip().lower()
        for card, p_path, p_base, _ in self.project_item_widgets:
            match = (not q) or (q in p_path.lower()) or (q in p_base.lower())
            card.setVisible(match)

    # ---------------------------------------------------------
    # Вкладка 4: Тайминги и Лимиты
    # ---------------------------------------------------------
    def create_timers_interface(self):
        scroll = ScrollArea()
        scroll.setObjectName("timersInterface")
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(36, 24, 36, 24)
        vbox.setSpacing(22)

        # Заголовок страницы
        self.lbl_title_time = SubtitleLabel(self.mgr.tr("tab_timers"), container)
        vbox.addWidget(self.lbl_title_time)

        self.grp_timers = SettingCardGroup(self.mgr.tr("grp_timers"), container)

        # 1. Пауза перед возобновлением
        retry_val = float(self.mgr.settings.get("retry_pause_seconds", 10.0))
        self.card_retry = CustomDoubleSpinCard(
            FIF.SPEED_HIGH,
            self.mgr.tr("lbl_retry_pause"),
            self.mgr.tr("desc_retry_pause"),
            min_val=0.5, max_val=300.0, cur_val=retry_val,
            step=0.5, decimals=1,
            unit=self.mgr.tr("unit_seconds"),
            parent=self.grp_timers
        )
        self.card_retry.valueChanged.connect(lambda v: self._on_timer_changed("retry_pause_seconds", float(v)))
        self.grp_timers.addSettingCard(self.card_retry)

        # 2. Кулдаун после возобновления
        cd_val = float(self.mgr.settings.get("post_resume_cooldown", 6.0))
        self.card_cooldown = CustomDoubleSpinCard(
            FIF.STOP_WATCH,
            self.mgr.tr("lbl_cooldown"),
            self.mgr.tr("desc_cooldown"),
            min_val=0.5, max_val=300.0, cur_val=cd_val,
            step=0.5, decimals=1,
            unit=self.mgr.tr("unit_seconds"),
            parent=self.grp_timers
        )
        self.card_cooldown.valueChanged.connect(lambda v: self._on_timer_changed("post_resume_cooldown", float(v)))
        self.grp_timers.addSettingCard(self.card_cooldown)

        # 3. Частота опроса
        cur_poll = float(self.mgr.settings.get("poll_interval_seconds", 1.0))
        self.card_poll = CustomDoubleSpinCard(
            FIF.SYNC,
            self.mgr.tr("lbl_poll_interval"),
            self.mgr.tr("desc_poll_interval"),
            min_val=0.1, max_val=60.0, cur_val=cur_poll,
            step=0.1, decimals=1,
            unit=self.mgr.tr("unit_seconds"),
            parent=self.grp_timers
        )
        self.card_poll.valueChanged.connect(lambda v: self._on_timer_changed("poll_interval_seconds", float(v)))
        self.grp_timers.addSettingCard(self.card_poll)

        # 4. Максимум попыток подряд
        retries_val = int(self.mgr.settings.get("max_retries_consecutive", 10))
        self.card_retries = CustomSpinCard(
            FIF.UPDATE,
            self.mgr.tr("lbl_max_retries"),
            self.mgr.tr("desc_max_retries"),
            min_val=0, max_val=1000, cur_val=retries_val,
            step=1,
            unit=self.mgr.tr("unit_times"),
            parent=self.grp_timers
        )
        self.card_retries.valueChanged.connect(lambda v: self._on_timer_changed("max_retries_consecutive", int(v)))
        self.grp_timers.addSettingCard(self.card_retries)

        vbox.addWidget(self.grp_timers)
        vbox.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _on_timer_changed(self, key, val):
        self.mgr.settings[key] = val
        self.mgr.save()
        self.settings_saved.emit()

    # ---------------------------------------------------------
    # Вкладка 5: Журнал событий
    # ---------------------------------------------------------
    def create_journal_interface(self):
        scroll = ScrollArea()
        scroll.setObjectName("journalInterface")
        scroll.setWidgetResizable(True)

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(36, 24, 36, 24)
        vbox.setSpacing(18)

        # Заголовок страницы
        self.lbl_title_journal = SubtitleLabel(self.mgr.tr("tab_journal"), container)
        vbox.addWidget(self.lbl_title_journal)

        # Карточки сводной статистики
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(16)

        # 1. Возобновлений целей
        self.card_stat_resumes = CardWidget(container)
        c1 = QVBoxLayout(self.card_stat_resumes)
        c1.setContentsMargins(18, 16, 18, 16)
        self.lbl_stat_resumes_title = CaptionLabel(self.mgr.tr("journal_stat_resumes"), self.card_stat_resumes)
        self.lbl_stat_resumes_title.setTextColor(QColor("#a6adc8"), QColor("#a6adc8"))
        self.lbl_stat_resumes_val = SubtitleLabel("0", self.card_stat_resumes)
        self.lbl_stat_resumes_val.setTextColor(QColor("#70d6ff"), QColor("#70d6ff"))
        c1.addWidget(self.lbl_stat_resumes_title)
        c1.addWidget(self.lbl_stat_resumes_val)
        stats_layout.addWidget(self.card_stat_resumes)

        # 2. Перехвачено сбоев
        self.card_stat_errors = CardWidget(container)
        c2 = QVBoxLayout(self.card_stat_errors)
        c2.setContentsMargins(18, 16, 18, 16)
        self.lbl_stat_errors_title = CaptionLabel(self.mgr.tr("journal_stat_errors"), self.card_stat_errors)
        self.lbl_stat_errors_title.setTextColor(QColor("#a6adc8"), QColor("#a6adc8"))
        self.lbl_stat_errors_val = SubtitleLabel("0", self.card_stat_errors)
        self.lbl_stat_errors_val.setTextColor(QColor("#f38ba8"), QColor("#f38ba8"))
        c2.addWidget(self.lbl_stat_errors_title)
        c2.addWidget(self.lbl_stat_errors_val)
        stats_layout.addWidget(self.card_stat_errors)

        # 3. Статус сторожа
        self.card_stat_status = CardWidget(container)
        c3 = QVBoxLayout(self.card_stat_status)
        c3.setContentsMargins(18, 16, 18, 16)
        self.lbl_stat_status_title = CaptionLabel(self.mgr.tr("journal_stat_status"), self.card_stat_status)
        self.lbl_stat_status_title.setTextColor(QColor("#a6adc8"), QColor("#a6adc8"))
        self.lbl_stat_status_val = SubtitleLabel(self.mgr.tr("journal_status_active"), self.card_stat_status)
        self.lbl_stat_status_val.setTextColor(QColor("#a6e3a1"), QColor("#a6e3a1"))
        c3.addWidget(self.lbl_stat_status_title)
        c3.addWidget(self.lbl_stat_status_val)
        stats_layout.addWidget(self.card_stat_status)

        vbox.addLayout(stats_layout)

        # Панель действий
        act_card = CardWidget(container)
        act_layout = QHBoxLayout(act_card)
        act_layout.setContentsMargins(18, 14, 18, 14)
        act_layout.setSpacing(12)

        self.search_journal = SearchLineEdit(act_card)
        self.search_journal.setPlaceholderText(self.mgr.tr("journal_search_placeholder"))
        self.search_journal.textChanged.connect(self.filter_journal_events)
        act_layout.addWidget(self.search_journal, 1)

        self.btn_refresh_journal = PushButton(FIF.SYNC, self.mgr.tr("journal_btn_refresh"), act_card)
        self.btn_refresh_journal.clicked.connect(self.refresh_journal_events)
        act_layout.addWidget(self.btn_refresh_journal)

        self.btn_open_log = PushButton(FIF.DOCUMENT, self.mgr.tr("journal_btn_open_log"), act_card)
        self.btn_open_log.clicked.connect(self.open_external_log)
        act_layout.addWidget(self.btn_open_log)

        self.btn_clear_journal = PushButton(FIF.DELETE, self.mgr.tr("journal_btn_clear"), act_card)
        self.btn_clear_journal.clicked.connect(self.clear_journal_log)
        act_layout.addWidget(self.btn_clear_journal)

        vbox.addWidget(act_card)

        # Список событий
        self.grp_journal = SettingCardGroup(self.mgr.tr("nav_journal"), container)
        vbox.addWidget(self.grp_journal)
        self.journal_item_widgets = []

        vbox.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def refresh_journal_events(self):
        while self.grp_journal.cardLayout.count():
            item = self.grp_journal.cardLayout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.journal_item_widgets.clear()

        tr = self.mgr.tr
        resumes_count = 0
        errors_count = 0
        lines = []

        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                for line in all_lines:
                    l_lower = line.lower()
                    if "возобновления" in l_lower or "возобновление" in l_lower or "resumed" in l_lower or "invokepattern" in l_lower:
                        resumes_count += 1
                    if "[error]" in l_lower or "сбоя" in l_lower or "ошибка" in l_lower:
                        errors_count += 1
                lines = all_lines[-60:]
            except Exception:
                pass

        self.lbl_stat_resumes_val.setText(str(resumes_count))
        self.lbl_stat_errors_val.setText(str(errors_count))

        if not lines:
            empty_card = SettingCard(FIF.DOCUMENT, tr("journal_empty"), "", parent=self.grp_journal)
            self.grp_journal.addSettingCard(empty_card)
            return

        for line in reversed(lines):
            line_str = line.strip()
            if not line_str or line_str.startswith("==="):
                continue

            # Игнорируем сырой вывод стектрейсов Python во избежание захламления журнала
            if (line_str.startswith("Traceback (") or 
                line_str.startswith("File \"") or 
                line_str.startswith("^^^^") or 
                line_str.startswith("pywintypes.error")):
                continue

            ts = ""
            msg = line_str
            if line_str.startswith("[") and "]" in line_str:
                parts = line_str.split("]", 2)
                if len(parts) >= 2:
                    ts = parts[0].strip("[")
                    msg = parts[-1].strip()
                    if msg.startswith("["):
                        msg = msg.split("]", 1)[-1].strip()

            l_low = msg.lower()
            card_tooltip = line_str

            if "возобновлен" in l_low or "resum" in l_low or "invokepattern" in l_low or "dodefaultaction" in l_low:
                icon = FIF.SPEED_HIGH
                badge_color = "#a6e3a1"
                badge_bg = "rgba(166, 227, 161, 0.15)"

                m_chat = re.search(r"Чат:\s*[«'\"](.*?)[\"»']", msg, re.IGNORECASE)
                chat_name = m_chat.group(1).strip() if m_chat else ""

                m_goal = re.search(r"(?:Цель|Задача|Goal|Task):\s*[«'\"](.*?)[\"»']", msg, re.IGNORECASE)
                goal_text = m_goal.group(1).strip() if m_goal else ""

                m_btn = re.search(r"кнопка:\s*['\"](.*?)['\"]", msg, re.IGNORECASE) or re.search(r"\('([^']+)'\)", msg)
                btn_name = m_btn.group(1).strip() if m_btn else ""

                m_cnt = re.search(r"(?:Всего|Total):\s*(\d+)", msg, re.IGNORECASE)
                cnt_val = m_cnt.group(1).strip() if m_cnt else ""

                is_task_mode = ("задача" in l_low or "task" in l_low or "(ответ)" in l_low)
                base_title = tr("journal_event_resume_task") if is_task_mode else tr("journal_event_resume_goal")

                if chat_name:
                    event_title = f"{base_title}: {chat_name}"
                else:
                    event_title = base_title

                parts_desc = []
                if goal_text:
                    prefix = tr("journal_prefix_task") if is_task_mode else tr("journal_prefix_goal")
                    parts_desc.append(f"{prefix}: «{goal_text}»")
                if btn_name:
                    parts_desc.append(f"{tr('journal_prefix_btn')}: '{btn_name}'")
                if cnt_val:
                    parts_desc.append(f"{tr('journal_prefix_total')}: {cnt_val}")

                if parts_desc:
                    msg = "  •  ".join(parts_desc)
                if goal_text:
                    card_tooltip = f"{chat_name}\n{goal_text}" if chat_name else goal_text

            elif "кнопка" in l_low or "обнаружена" in l_low or "найдена кнопка" in l_low:
                icon = FIF.PLAY
                event_title = tr("journal_event_trigger")
                badge_color = "#70d6ff"
                badge_bg = "rgba(112, 214, 255, 0.15)"

            elif "переключено" in l_low or "смена чата" in l_low or "switch" in l_low:
                icon = FIF.FOLDER
                badge_color = "#cba6f7"
                badge_bg = "rgba(203, 166, 247, 0.15)"
                m_chat = re.search(r"чате\s*['\"](.*?)['\"]", msg, re.IGNORECASE) or re.search(r"Чат:\s*[«'\"](.*?)[\"»']", msg, re.IGNORECASE)
                chat_name = m_chat.group(1).strip() if m_chat else ""
                if chat_name:
                    event_title = f"{tr('journal_event_switch')}: {chat_name}"
                else:
                    event_title = tr("journal_event_switch")

            elif "[error]" in l_low or "ошибка" in l_low:
                icon = FIF.INFO
                event_title = tr("journal_event_error")
                badge_color = "#f38ba8"
                badge_bg = "rgba(243, 139, 168, 0.15)"

            elif "переключено:" in l_low or "режим" in l_low:
                icon = FIF.UPDATE
                event_title = tr("journal_event_state")
                badge_color = "#fab387"
                badge_bg = "rgba(250, 179, 135, 0.15)"

            else:
                icon = FIF.DOCUMENT
                event_title = tr("journal_event_start")
                badge_color = "#a6adc8"
                badge_bg = "rgba(166, 173, 200, 0.15)"

            card = SettingCard(icon, event_title, msg, parent=self.grp_journal)
            card.setToolTip(card_tooltip)

            if ts:
                ts_lbl = QLabel(ts)
                ts_lbl.setStyleSheet(f"""
                    QLabel {{
                        color: {badge_color};
                        background-color: {badge_bg};
                        border: 1px solid {badge_color};
                        border-radius: 4px;
                        padding: 3px 8px;
                        font-size: 11px;
                        font-family: 'Consolas', 'Segoe UI Mono', monospace;
                    }}
                """)
                ts_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card.hBoxLayout.insertWidget(0, ts_lbl)
                card.hBoxLayout.insertSpacing(1, 14)

            self.grp_journal.addSettingCard(card)
            self.journal_item_widgets.append((card, line_str))

    def filter_journal_events(self, query):
        q = query.strip().lower()
        for card, raw_line in self.journal_item_widgets:
            card.setVisible(not q or q in raw_line.lower())

    def open_external_log(self):
        if os.path.exists(LOG_FILE):
            try:
                os.startfile(LOG_FILE)
            except Exception:
                pass

    def clear_journal_log(self):
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            self.refresh_journal_events()
        except Exception:
            pass

    # ---------------------------------------------------------
    # Загрузка и сохранение настроек
    # ---------------------------------------------------------
    def load_values(self):
        s = self.mgr.settings

        # Язык
        lang_code = s.get("language", "auto")
        codes = ["auto", "ru", "en"]
        if lang_code in codes:
            self.card_lang.combo.blockSignals(True)
            self.card_lang.setCurrentIndex(codes.index(lang_code))
            self.card_lang.combo.blockSignals(False)

        # Запуск и оповещения (блокируем сигналы, чтобы не сохранять повторно при загрузке)
        cards = [
            (self.card_autostart, bool(s.get("autostart_windows", True))),
            (self.card_codex_start, bool(s.get("enable_on_codex_start", True))),
            (self.card_notif_toggle, bool(s.get("notify_on_toggle", True))),
            (self.card_notif_resume, bool(s.get("notify_on_resume", False))),
            (self.card_sound_resume, bool(s.get("sound_on_resume", False))),
        ]
        for card, val in cards:
            card.switchButton.blockSignals(True)
            card.setChecked(val)
            card.update_switch_texts()
            card.switchButton.blockSignals(False)

        # Тайминги
        self.card_retry.setValue(float(s.get("retry_pause_seconds", 10.0)))
        self.card_cooldown.setValue(float(s.get("post_resume_cooldown", 6.0)))
        self.card_poll.setValue(float(s.get("poll_interval_seconds", 1.0)))
        self.card_retries.setValue(int(s.get("max_retries_consecutive", 10)))

        self.refresh_error_cards()
        self.refresh_project_cards()
        self.refresh_journal_events()

    def _quick_save(self, key, val):
        self.mgr.settings[key] = val
        self.mgr.save()
        self.settings_saved.emit()

    def _sync_autostart(self, checked):
        self.mgr.settings["autostart_windows"] = checked
        self.mgr.save()
        self.settings_saved.emit()
        try:
            pyw = sys.executable.replace("python.exe", "pythonw.exe")
            app_py = os.path.join(APP_DIR, "codex_tray_app.py")
            if checked:
                vbs = f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run """{pyw}"" """{app_py}""", 0, False\n'
                os.makedirs(os.path.dirname(AUTOSTART_VBS), exist_ok=True)
                with open(AUTOSTART_VBS, "w", encoding="utf-8") as f:
                    f.write(vbs)
            else:
                if os.path.exists(AUTOSTART_VBS):
                    os.remove(AUTOSTART_VBS)
        except Exception:
            pass

    def on_language_changed(self, idx):
        codes = ["auto", "ru", "en"]
        if 0 <= idx < len(codes):
            self.mgr.settings["language"] = codes[idx]
            self.mgr.save()
            self.apply_translations()

    def apply_translations(self):
        tr = self.mgr.tr
        self.setWindowTitle(tr("dialog_title"))

        # Обновление заголовков
        self.lbl_title_gen.setText(tr("tab_general"))
        self.lbl_title_err.setText(tr("tab_errors"))
        self.lbl_title_proj.setText(tr("tab_projects"))
        self.lbl_title_time.setText(tr("tab_timers"))
        self.lbl_title_journal.setText(tr("tab_journal"))

        # Язык
        lang_items = [self.get_auto_language_label(), tr("lang_ru"), tr("lang_en")]
        self.card_lang.update_texts(tr("grp_language"), tr("lang_desc"), lang_items)

        # Запуск
        self.card_autostart.setTitle(tr("chk_autostart_windows"))
        self.card_autostart.setContent(tr("desc_autostart_windows"))
        self.card_autostart.update_switch_texts()

        self.card_codex_start.setTitle(tr("chk_enable_on_codex_start"))
        self.card_codex_start.setContent(tr("desc_enable_on_codex_start"))
        self.card_codex_start.update_switch_texts()

        # Оповещения
        self.card_notif_toggle.setTitle(tr("chk_notify_toggle"))
        self.card_notif_toggle.setContent(tr("desc_notify_toggle"))
        self.card_notif_toggle.update_switch_texts()

        self.card_notif_resume.setTitle(tr("chk_notify_resume"))
        self.card_notif_resume.setContent(tr("desc_notify_resume"))
        self.card_notif_resume.update_switch_texts()

        self.card_sound_resume.setTitle(tr("chk_sound_resume"))
        self.card_sound_resume.setContent(tr("desc_sound_resume"))
        self.card_sound_resume.update_switch_texts()

        # Ошибки
        self.lbl_desc_err.setText(tr("desc_errors_info"))
        self.edit_new_error.setPlaceholderText(tr("placeholder_new_error"))
        self.btn_add_error.setText(tr("btn_add_error"))
        self.btn_reset_errors.setText(tr("btn_reset_errors"))
        self.refresh_error_cards()

        # Проекты
        self.lbl_desc_proj.setText(tr("lbl_projects_desc"))
        self.search_projects.setPlaceholderText(tr("search_projects_placeholder"))
        self.btn_select_all_proj.setText(tr("btn_select_all"))
        self.btn_deselect_all_proj.setText(tr("btn_deselect_all"))
        self.update_projects_count_label()

        # Тайминги
        self.card_retry.update_texts(tr("lbl_retry_pause"), tr("desc_retry_pause"), tr("unit_seconds"))
        self.card_cooldown.update_texts(tr("lbl_cooldown"), tr("desc_cooldown"), tr("unit_seconds"))
        self.card_poll.update_texts(tr("lbl_poll_interval"), tr("desc_poll_interval"), tr("unit_seconds"))
        self.card_retries.update_texts(tr("lbl_max_retries"), tr("desc_max_retries"), tr("unit_times"))

        # Журнал
        self.lbl_stat_resumes_title.setText(tr("journal_stat_resumes"))
        self.lbl_stat_errors_title.setText(tr("journal_stat_errors"))
        self.lbl_stat_status_title.setText(tr("journal_stat_status"))
        self.search_journal.setPlaceholderText(tr("journal_search_placeholder"))
        self.btn_refresh_journal.setText(tr("journal_btn_refresh"))
        self.btn_open_log.setText(tr("journal_btn_open_log"))
        self.btn_clear_journal.setText(tr("journal_btn_clear"))
        self.refresh_journal_events()

        # Обновление навигации
        key_map = {
            "generalInterface": tr("nav_general"),
            "errorsInterface": tr("nav_errors"),
            "projectsInterface": tr("nav_projects"),
            "timersInterface": tr("nav_timers"),
            "journalInterface": tr("nav_journal"),
        }
        for btn in self.nav.buttons():
            rk = btn.property("routeKey")
            if rk in key_map:
                btn.setText(key_map[rk])

        self.settings_saved.emit()


# Совместимость с кодом трея
class SettingsDialog:
    def __init__(self, parent=None):
        pass

def open_settings_dialog():
    attach_desktop()

    hwnd = win32_find_settings_window()
    if hwnd:
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(hwnd)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return

    app = QApplication.instance()
    is_standalone = False
    if not app:
        app = QApplication(sys.argv)
        is_standalone = True

    setTheme(Theme.DARK)

    win = SettingsWindow()
    win.show()

    if is_standalone:
        sys.exit(app.exec())

def win32_find_settings_window():
    import win32gui
    found = []
    def cb(h, _):
        txt = win32gui.GetWindowText(h)
        if any(txt.startswith(t) for t in ("Настройки Codex Auto-Resume", "Codex Auto-Resume Settings")):
            found.append(h)
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    return found[0] if found else None

if __name__ == "__main__":
    open_settings_dialog()
