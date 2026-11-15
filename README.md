# ACC Race Engineer

A local-first native Windows pit wall for Assetto Corsa Competizione (ACC). The primary app is a Windows desktop interface built with Qt; it does not open a browser or embed a web view. A private loopback FastAPI service runs inside the application as its local backend and is not the user interface.

The app reads ACC telemetry, applies deterministic event and fuel calculations, plays approved local WAV clips, and can send optional radio requests to OpenRouter. It includes a personal engineer profile, local searchable memory, and an event-grounded call director. This is an early release: a short historical ACC spot check exists, but full live compatibility, radio timing and warning quality have not been accepted. Read [validation limits](docs/VALIDATION.md) and [engineer behaviour](docs/ENGINEER_BEHAVIOUR.md) before relying on it in a race.

## Requirements

- Windows 10/11 and Python 3.11 or 3.13.
- ACC for live shared-memory telemetry. ACC broadcasting is optional.
- Network access and an OpenRouter key only for provider-backed radio text/speech.

## Install and launch

From a PowerShell prompt in the project directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[radio,vision]"
.\Start-Engineer.ps1
```

Python 3.11 can be selected with `py -3.11` instead. `Start-Engineer.ps1` creates `.venv` if needed, checks the native UI and optional radio/vision imports, installs `.[radio,vision]` when they are missing, then starts the native app without a console window. Close the ACC Race Engineer window to exit. For a smaller development install without microphone/controller or screenshot capture, install `.[test]` and launch with `.\.venv\Scripts\python.exe -m acc_engineer`.

The app's backend binds only to `127.0.0.1` on an ephemeral local port, managed by the desktop app. It is not intended to be exposed on a network. Runtime data (SQLite database and generated audio) is stored under `%LOCALAPPDATA%\ACC Race Engineer` by default. Set `ACC_ENGINEER_DATA` before launch to choose another data directory.

## What it does

- Polls ACC shared memory read-only while the game is running; stale or absent data is treated as unknown.
- Evaluates configurable race events and a conservative, measurement-based fuel projection. Estimates need completed lap samples and are not guarantees.
- Provides native desktop pages for telemetry, events, activity, provider usage, audio clips and settings. The local backend supports the native interface; any legacy dashboard route is diagnostic and is not opened by the launcher.
- Includes a **Personal engineer** page for the driver's name, encouragement level, explicit profanity preference, and supported driving preferences. Profile and approved session memory live in the local SQLite database.
- Plays approved WAV clips locally. Text-to-speech generation is optional and uses OpenRouter's audio speech endpoint.
- Uses a deterministic director for proactive, measured race calls. Approved critical clips are processed locally and can preempt lower-priority queued audio. Emotion follows verified events; the model does not determine race outcomes or invent damage.
- Supports explicit radio toggle recording (press once to record, again to send), local faster-whisper transcription, and XInput/keyboard controls when the radio extras and compatible devices are present.
- Optional follow-up listening is off by default. If enabled, it listens for up to five seconds only after a spoken engineer question has finished playing. There is no always-on microphone mode.
- On explicit request, the app can capture one screenshot of the foreground ACC client area on the primary monitor and send it for model analysis. Capture is not run in the background, and the screenshot is not persisted by the app. Install the `vision` extra to enable capture.
- Can listen to ACC broadcasting on loopback for competitor data when configured.
- Can inspect and create a copy of a setup JSON from a configured directory using a user-supplied profile explicitly marked validated. It backs up the source and does not load the result in ACC.

The model can use six read-only telemetry tools for tyre status, fuel projection, competitor gaps, recent incidents, weather and lap comparison, plus a selective local-memory search tool. The local-memory tool runs only when requested by the model; the initial prompt contains a small index manifest, not stored conversations or profile text. Strategy advice uses measured fuel, tyre, brake and ACC weather readings; there is no verified pit-loss or optimal undercut/overcut model. Setup parameter mappings, conversions, event thresholds and in-game interpretations require external validation for each car/track. See [validation limits](docs/VALIDATION.md), [engineer behaviour](docs/ENGINEER_BEHAVIOUR.md) and [ACC integration notes](docs/ACC_INTEGRATION.md).

## Provider credentials and radio

Configure an OpenRouter API key in the app's AI settings. The key is stored with Windows Credential Manager through `keyring`, not in the SQLite settings database. Alternatively, set `OPENROUTER_API_KEY` in the process environment; environment credentials take precedence. Do not put credentials in project files or commit them.

The default text model is `openai/gpt-6-luna`. The default speech model is `fish-audio/drama-3`; the engineer is pinned to Fish Official Adrian by a fixed voice ID, including when the field is blank. Enter another supported voice ID to deliberately change the engineer. These model identifiers and the speech endpoint were checked against the live OpenRouter model/endpoint catalogue during this release's preparation; availability, pricing and provider behavior can change. Requests are blocked when model pricing is unavailable or when the configured per-request/weekly local expenditure limits would be exceeded. Provider-reported billing is authoritative; local bounds are conservative estimates. See [provider details](docs/PROVIDERS.md).

For microphone push-to-talk, install the `radio` extra. For screenshot analysis, install `vision` with `python -m pip install -e ".[vision]"`. The first local faster-whisper transcription downloads the selected model (currently `base.en`) to the local model cache. Microphone recording is explicit and bounded; the optional five-second follow-up mode is separately controlled in settings and defaults off.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The GitHub Actions workflow runs pytest on Windows with Python 3.11 and 3.13. The offline suite includes native Qt, runtime orchestration, persistent memory and synthetic telemetry checks. Tests do not establish current live ACC acceptance, clean-machine installation or race performance; see [validation limits](docs/VALIDATION.md).

To build a standalone folder distribution, run `.Build-Engineer.ps1`. Launch `dist\ACC-Race-Engineer\ACC-Race-Engineer.exe`; it opens the native desktop app and starts its local backend. This PyInstaller build has not been validated on a clean Windows machine.

## Safety and limitations

The application does not write to ACC shared memory, modify ACC configuration, automatically rebind controls, load a setup into the game, or upload setup changes. Broadcasting registration is read-only. Verify control conflicts manually. Treat all calculations and generated speech as assistance and confirm critical race decisions using ACC's own displays.
