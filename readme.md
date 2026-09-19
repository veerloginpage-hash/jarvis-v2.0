# 🤖 VEER INDUS
### A fast, verifiable, voice-first personal AI assistant

A real-time voice AI that can hear, see, understand, and control your computer. VEER INDUS is designed around fast direct tool use, visible verification, recovery, and a responsive control-room UI.

---

## ✨ Overview

VEER INDUS bridges the gap between human intent and the operating system. It can analyze the screen, process uploaded documents, operate browsers and desktop apps, and execute multi-step workflows while keeping an observable runtime ledger.

It's not just an assistant — it's an extension of your digital life.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🎙️ Real-time Voice | Low-latency Gemini Live conversation with microphone and speaker recovery |
| 🖥️ System Control | Launch apps, manage files, execute terminal commands |
| 🧩 Direct Task Tools | Fast browser, file, app, and system actions |
| 👁️ Visual Awareness | Screen/webcam analysis plus local vision fallback |
| ✅ Action Verification | Visible click actions are checked before success is reported |
| 🧠 Persistent Memory | Persistent preferences, history, runtime checkpoints, and session context |
| ⌨️ Hybrid Input | Seamlessly switch between keyboard typing and voice commands |
| ⚡ Fast Paths | Common open/search/weather/navigation commands skip unnecessary planning calls |
| ⏰ Automation Engine | Named daily, weekly, and one-time routines with pause/resume, run history, and OS-level scheduling |
| 🧭 Local Workflows | Free, repeatable Windows UI Automation with visible-control selectors |

---

## 🆕 VEER INDUS highlights

- 📂 **Advanced File Handling** — New support for direct file uploads. Drop PDFs, source code, or images into the assistant to have them analyzed, summarized, or edited instantly.
- 🎨 **Adaptive & Flexible UI** — A complete overhaul of the interface. The new UI is fully resizable and responsive, featuring transparency controls and customizable layouts to fit your workspace perfectly.
- 🐧🍎 **Refined Cross-Platform Stability** — Major fixes for macOS and Linux compatibility. Core system actions are now more consistent across all three major operating systems.
- ⚡ **Optimized Core Engine** — Deterministic commands use local fast paths; network-heavy work remains asynchronous.
- 🧪 **Regression Checks** — Fast intent parsing and task lifecycle behavior are covered by lightweight tests.
- 🔁 **Recovery-first execution** — Failed actions return explicit failure state instead of silently claiming completion.

---

## ⚡ Quick Start

```bash
git clone <your-repository-url>
cd veer-indus
pip install -r requirements.txt
playwright install
python main.py
```

> ⚠️ **Installation Note:** To keep the repository lightweight, some OS-specific dependencies are not bundled in `requirements.txt`. If you run into a `ModuleNotFoundError`, simply install the missing package via `pip install <module_name>` for your specific system.

### Optional audio device preferences

To prefer a specific microphone or output such as AirPods, add the device-name fragment to `config/api_keys.json`:

```json
{
  "audio_input_device": "AirPods",
  "audio_output_device": "AirPods"
}
```

Names are matched case-insensitively. If the preferred device is disconnected, VEER INDUS automatically falls back to the operating-system default device and reports the active device in its health check.

### Automations

J.A.R.V.I.S can schedule reusable routines such as “every weekday at 08:30, open
my calendar and prepare a daily brief.” Each routine is explicitly named, stored
locally, and can be listed, run immediately, paused, resumed, or deleted. The
assistant must show the exact goal and schedule for your approval before it
creates an unattended routine. Routine definitions and their recent outcomes are
kept in `~/.jarvis/automations/`.

### Local Workflows

Local Workflows are free and have no cloud or per-request API cost. Each saved
workflow uses deterministic steps such as open an app, focus a window, locate a
native Windows UI control by its visible label, click it, type, press a key, wait,
or verify that a control exists. A routine stops at the first missing control
instead of guessing a screen coordinate and clicking the wrong place.

---

## 📋 Requirements

| Requirement | Details |
|---|---|
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction |
| **API Key** | Free Gemini API key |

---

## ⚠️ License

Personal and non-commercial use only.
Licensed under **[Creative Commons BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)**.

---

## 👤 Connect with the Creator

Engineered as a real-world JARVIS-style assistant under the VEER INDUS identity.

| Platform | Link |
|---|---|
| YouTube | [@FatihMakes](https://www.youtube.com/@FatihMakes) |
| Instagram | [@fatihmakes](https://www.instagram.com/fatihmakes) |
