# Codex Auto-Resume Watchdog v2.0

[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4.svg?style=flat-square&logo=windows)](https://microsoft.com)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10%2B-3776ab.svg?style=flat-square&logo=python)](https://python.org)
[![UI: PyQt6 & QFluentWidgets](https://img.shields.io/badge/ui-Fluent%20Design%20(Dark)-6c5ce7.svg?style=flat-square)](https://qfluentwidgets.com)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)

An intelligent, non-intrusive background guardian for **OpenAI Codex / ChatGPT Desktop** on Windows. Automatically detects interrupted generation errors (*Model capacity overloads, Remote compact task failures, Stream disconnects, Rate limits*) and silently resumes active goals and tasks in the background **without stealing focus or minimizing borderless fullscreen games**.

---

## 🌐 Language Navigation / Навигация по языкам
- [English Documentation](#-english-documentation)
  - [Key Features](#-key-features)
  - [Settings Overview](#-settings-overview-fluent-design)
  - [Architecture & Reliability](#-architecture--reliability)
  - [Download & Installation](#-download--installation)
  - [Building from Source](#-building-from-source)
- [Русская документация](#-русская-документация)
  - [Основные возможности](#-основные-возможности)
  - [Обзор окна настроек](#-обзор-окна-настроек-fluent-design)
  - [Архитектура и надежность](#-архитектура-и-надежность)
  - [Скачивание и установка](#-скачивание-и-установка)
  - [Сборка из исходников](#-сборка-из-исходного-кода)

---

# 🇺🇸 English Documentation

## ⚡ Key Features

- **🎮 Zero-Interruption Background Execution:**
  Directly interacts with the Codex UI Automation layer (`IUIAutomationInvokePattern` / `IUIAutomationLegacyIAccessiblePattern`). Automatically resumes tasks without bringing windows to foreground or minimizing borderless fullscreen games.
- **👥 Multiple Codex Accounts:**
  Every running Codex window is watched separately with its own `CODEX_HOME` (e.g. `~/.codex` and `~/.codex-account2`), so error detection and resume decisions use the right account's history. The tray tooltip shows what is watched per account (`watching` / `generating` / `button` / `window hidden ⚠`); an amber `!` icon means nothing is watched or an account window is hidden in the tray.
- **✨ Official Codex Branding with Live Status Badges:**
  High-resolution official Codex glyph on a dark squircle with crisp cutout indicator badges:
  - 🟢 **Active / Enabled:** Emerald green circle with checkmark (`✓`).
  - ⚪ **Paused / Disabled:** Slate gray circle with pause bars (`||`).
  - 🔵 **Resuming / Working:** Sky blue circle with play arrow (`▶`).
- **🎛️ Windows 11 Fluent Design Settings Window:**
  Built with `QFluentWidgets` with native Windows 11 immersive dark mode (`DWMWA_USE_IMMERSIVE_DARK_MODE`).
- **💾 Real-Time Auto-Save:**
  All adjustments, switches, and spinboxes persist instantaneously to disk without requiring manual "Save" button clicks.
- **📌 Windows Taskbar Integration:**
  Explicit `AppUserModelID` (`mrpanica.codex.autoresume.v2`) ensures the application displays the authentic Codex icon on the Windows taskbar instead of the generic Python interpreter icon.
- **🌍 Bilingual & Dynamic Language Support:**
  Automatic detection of the Windows system language (e.g. `Automatic (Russian)` or `Automatic (English)`) with on-the-fly language switching between Russian and English.

---

## 🛠️ Settings Overview (Fluent Design)

The settings window is divided into five dedicated sections:

1. **General & Startup Options (`Общие`):**
   - **Interface Language:** Auto-detects system language or manually selects Russian / English.
   - **Start with Windows:** Configures automatic silent startup via VBScript in the Windows Startup folder.
   - **Enable on Codex Start:** Automatically shifts watchdog from paused to active mode when the `ChatGPT.exe` process is launched.
   - **Notifications & Sounds:** Configurable desktop banners and audible chimes upon resume or toggle.

2. **Monitored Error Patterns (`Ошибки`):**
   - Standard built-in error filters:
     - `selected model is at capacity` (Model Capacity / Server Overload)
     - `error running remote compact task` (Remote Compact Task Failure)
     - `stream disconnected` (Network Stream Loss)
     - `connection lost` (Connection Drop)
     - `rate limit` (Rate Limit Exceeded)
     - `timeout` (Request Timeout)
     - `an unexpected error occurred` (Unexpected Server Error)
     - `serveroverloaded` (Server Overloaded Code)
   - Add custom regex/string patterns with one click.
   - Individual toggle switches ("Вкл" / "Выкл") for each pattern.

3. **Participating Codex Projects (`Проекты`):**
   - Automatically discovers active and recent projects from `session_index.jsonl`.
   - Real-time project search by name or directory path.
   - Quick **Select All** and **Deselect All** action buttons.

4. **Delay Timings & Limits (`Тайминги`):**
   - **Delay Before Resuming:** Numeric spin input with `0.5s` increments (default: `10.0s`).
   - **Post-Resume Cooldown:** Numeric spin input with `0.5s` increments (default: `6.0s`).
   - **Polling Frequency:** Numeric spin input with `0.1s` increments (default: `1.0s`).
   - **Max Consecutive Retries:** Integer spin input (default: `10`, `0` = unlimited).

5. **Event Journal & Statistics (`Журнал`):**
   - **Live Metric Cards:** Resumed Goals count, Intercepted Errors count, Current Guardian Status.
   - **Event Log:** Chronological cards parsed from `guardian.log` with color-coded event badges and timestamps.
   - **Journal Actions:** Real-time log keyword search, Refresh, Open external log in default editor, Clear log.

---

## 🔬 Architecture & Reliability

- **Interactive Desktop Attachment (`SetThreadDesktop`):**
  When launched from background services, sandboxes, or remote agent environments, the application verifies the active thread desktop and automatically redirects execution to `Default`, ensuring zero UI virtualization crashes and reliable tray icon visibility.
- **Native Win32 Tray Context Menu:**
  The right-click context menu uses `TrackPopupMenuEx` on a dedicated message-only window handle, completely decoupling menu responsiveness from Qt event loop blocking.
- **Non-Stealing UIA Invocation:**
  Instead of simulating keyboard shortcuts (`Ctrl+Enter`) or mouse clicks (`SendInput`) which require stealing window focus, the watchdog accesses the Codex UI tree directly and invokes the action pattern (`Invoke()` / `DoDefaultAction()`).

---

## 📦 Download & Installation

### Option 1: Standalone Release (Recommended)
1. Download the latest `CodexAutoResume-v2.0-Windows.zip` from [GitHub Releases](https://github.com/MrPanica/codex-auto-resume/releases).
2. Extract the archive into any folder (e.g. `C:\Tools\CodexAutoResume\`).
3. Run `CodexAutoResume.exe`. The official Codex icon will appear in your system tray.

### Option 2: Running from Source
Requires Python 3.10+:
```powershell
# Clone repository
git clone https://github.com/MrPanica/codex-auto-resume.git
cd codex-auto-resume

# Install dependencies
pip install -r requirements.txt

# Run silently in background
pythonw codex_tray_app.py
```

---

## 🔨 Building from Source

To compile the standalone `CodexAutoResume.exe` and package the release ZIP:
```powershell
python build.py
```
Output artifacts:
- `dist/CodexAutoResume.exe`
- `release/CodexAutoResume-v2.0-Windows.zip`

---
---

# 🇷🇺 Русская документация

## ⚡ Основные возможности

- **🎮 Бесшумное возобновление без потери фокуса:**
  Прямое взаимодействие с интерфейсом Codex через UI Automation (`IUIAutomationInvokePattern` / `IUIAutomationLegacyIAccessiblePattern`). Автоматически возобновляет выполнение без сворачивания полноэкранных игр в borderless-режиме и без перехвата клавиатурного фокуса.
- **👥 Несколько аккаунтов Codex:**
  Каждое запущенное окно Codex наблюдается отдельно со своим `CODEX_HOME` (например, `~/.codex` и `~/.codex-account2`), поэтому ошибки и решения о возобновлении берутся из истории нужного аккаунта. Подсказка в трее показывает состояние по аккаунтам (`слежу` / `генерация` / `кнопка` / `окно скрыто ⚠`); жёлтый значок `!` означает, что ничего не наблюдается либо окно аккаунта скрыто в трей.
- **✨ Официальный логотип Codex с индикаторами статуса:**
  Четкий официальный векторный логотип Codex на темной подложке со скругленными углами и контрастным вырезом для бейджа:
  - 🟢 **Активен / ВКЛЮЧЕНО:** Изумрудно-зеленый круг с белой галочкой (`✓`).
  - ⚪ **Пауза / ВЫКЛЮЧЕНО:** Графитово-серый круг со значком паузы (`||`).
  - 🔵 **Возобновление в процессе:** Голубой круг со стрелкой (`▶`).
- **🎛️ Окно настроек Fluent Design в стиле Windows 11:**
  Построено на библиотеке `QFluentWidgets` с поддержкой темной системной темы (`DWMWA_USE_IMMERSIVE_DARK_MODE`).
- **💾 Автосохранение в реальном времени:**
  Любое изменение параметров, переключателей или чисел мгновенно сохраняется на диск без необходимости нажимать кнопку «Сохранить».
- **📌 Корректная иконка на панели задач Windows:**
  Регистрация системного идентификатора `AppUserModelID` (`mrpanica.codex.autoresume.v2`) устраняет отображение стандартной иконки `python.exe` на панели задач Windows.
- **🌍 Полная двуязычная локализация:**
  Автоматическое определение системного языка Windows (например, «Автоматически (Русский)») и поддержка мгновенного переключения между русским и английским языками.

---

## 🛠️ Обзор окна настроек (Fluent Design)

Интерфейс разделен на 5 функциональных вкладок:

1. **Общие параметры и запуск (`Общие`):**
   - **Язык интерфейса:** Автоопределение языка ОС либо ручной выбор (Русский / Английский).
   - **Запуск при старте Windows:** Интеграция с папкой автозагрузки Windows через бесшумный VBS-скрипт.
   - **Включать при старте Codex:** Автоматический перевод сторожа из паузы в активный режим при запуске `ChatGPT.exe`.
   - **Оповещения и звук:** Включение/отключение системных уведомлений Windows и звуковых сигналов при возобновлении.

2. **Отслеживаемые типы ошибок (`Ошибки`):**
   - Встроенные шаблоны ошибок:
     - `selected model is at capacity` (Перегрузка модели / сервера)
     - `error running remote compact task` (Сбой задачи сжатия Remote Compact)
     - `stream disconnected` (Разрыв стрим-соединения / сети)
     - `connection lost` (Потеря сетевого соединения)
     - `rate limit` (Превышен лимит запросов Rate Limit)
     - `timeout` (Таймаут ожидания ответа)
     - `an unexpected error occurred` (Неожиданная ошибка сервера)
     - `serveroverloaded` (Код перегрузки сервера ServerOverloaded)
   - Добавление собственных текстовых шаблонов сбоев в один клик.
   - Индивидуальные переключатели «Вкл» / «Выкл» для каждого правила.

3. **Участвующие проекты Codex (`Проекты`):**
   - Автоматический сбор проектов из индекса сессий `session_index.jsonl`.
   - Живой поиск проектов по наименованию или пути к папке.
   - Кнопки массового действия: **Выбрать все** и **Снять со всех**.

4. **Параметры задержек и повторов (`Тайминги`):**
   - **Пауза перед возобновлением:** Числовое поле с шагом `0.5` сек (по умолчанию: `10.0` сек).
   - **Кулдаун после возобновления:** Числовое поле с шагом `0.5` сек (по умолчанию: `6.0` сек).
   - **Частота опроса (Интервал):** Числовое поле с шагом `0.1` сек (по умолчанию: `1.0` сек).
   - **Максимум попыток подряд:** Целочисленное поле (по умолчанию: `10`, `0` = без ограничений).

5. **Журнал событий и статистика (`Журнал`):**
   - **Карточки сводки:** Счётчик возобновлений, счётчик перехваченных ошибок, статус сторожа.
   - **Лента событий:** Хронологический список последних записей из `guardian.log` со стилизованными бейджами времени.
   - **Управление журналом:** Поиск по записям, обновление списка, открытие файла лога в Блокноте, быстрая очистка журнала.

---

## 🔬 Архитектура и надежность

- **Подключение к интерактивному рабочему столу (`SetThreadDesktop`):**
  При запуске из служб или внешних агентов сторож проверяет десктоп потока и при необходимости подключается к `Default`, предотвращая сбои создания окон и контекстных меню трея.
- **Нативное контекстное меню Win32:**
  Вызов `TrackPopupMenuEx` гарантирует мгновенный отклик меню трея вне зависимости от фоновой нагрузки основного цикла Qt.
- **Фоновый предпрогрев окна настроек:**
  Экземпляр окна настроек подгружается в фоновом потоке, благодаря чему при клике «Настройки...» в трее окно появляется мгновенно без задержек.

---

## 📦 Скачивание и запуск

### Вариант 1: Готовый EXE-релиз (Рекомендуется)
1. Скачайте архив `CodexAutoResume-v2.0-Windows.zip` со страницы [GitHub Releases](https://github.com/MrPanica/codex-auto-resume/releases).
2. Распакуйте архив в удобную папку (например, `C:\Tools\CodexAutoResume\`).
3. Запустите `CodexAutoResume.exe`. Приложение появится в системном трее возле часов.

### Вариант 2: Запуск из исходного кода
Требуется Python 3.10 или новее:
```powershell
# Клонирование репозитория
git clone https://github.com/MrPanica/codex-auto-resume.git
cd codex-auto-resume

# Установка зависимостей
pip install -r requirements.txt

# Фоновый запуск
pythonw codex_tray_app.py
```

---

## 🔨 Сборка из исходного кода

Для компиляции автономного `CodexAutoResume.exe` и формирования релизного ZIP-архива выполните:
```powershell
python build.py
```
Собранные файлы будут сохранены в:
- `dist/CodexAutoResume.exe`
- `release/CodexAutoResume-v2.0-Windows.zip`

---

## 📄 Лицензия

Распространяется под лицензией MIT. Подробности в файле [LICENSE](LICENSE).
