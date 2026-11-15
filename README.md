# ACC Race Engineer

A local-first Windows pit wall for Assetto Corsa Competizione (ACC). It reads ACC telemetry, applies deterministic event and fuel calculations, plays approved local WAV clips, and can send optional radio requests to OpenRouter. The dashboard is served on loopback at `http://localhost:8000`.

This project is an early release. Shared memory and broadcast adapters have synthetic/unit coverage, but live compatibility with ACC, game-performance behavior, radio timing and warning quality have not been accepted or measured. Read [validation limits](docs/VALIDATION.md) before relying on it in a race.

## Requirements

- Windows 10/11 and Python 3.11 or 3.13.
- ACC for live shared-memory telemetry. ACC broadcasting is optional.
- Network access and an OpenRouter key only for provider-backed radio text/speech.

## Install and launch

From a PowerShell prompt in the project directory:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[radio,test]"
.\Start-Engineer.ps1
```

Python 3.11 can be selected with `py -3.11` instead. For a smaller install without microphone/controller support, install `.[test]` instead of `.[radio,test]`. The launcher creates `.venv` and installs the radio extras if that environment does not exist, then starts the service and opens the browser. Visit `http://localhost:8000` if the browser does not open. Stop the server with Ctrl+C in its PowerShell window.

The app binds to `127.0.0.1:8000`; it is not intended to be exposed on a network. Runtime data (SQLite database and generated audio) is stored under `%LOCALAPPDATA%\ACC Race Engineer` by default. Set `ACC_ENGINEER_DATA` before launch to choose another data directory.

## What it does

- Polls ACC shared memory read-only while the game is running; stale or absent data is treated as unknown.
- Evaluates configurable race events and a conservative, measurement-based fuel projection. Estimates need completed lap samples and are not guarantees.
- Serves a local dashboard to inspect telemetry, events, activity, provider usage, audio clips and settings.
- Plays approved WAV clips locally. Text-to-speech generation is optional and uses OpenRouter's audio speech endpoint.
- Supports explicit radio toggle recording (press once to record, again to send), local faster-whisper transcription, and XInput/keyboard controls when the radio extras and compatible devices are present.
- On explicit request, `/api/vision` can capture one screenshot of the foreground ACC client area on the primary monitor and send it for model analysis. Capture is not run in the background, and the screenshot is not persisted by the app. Install the `vision` extra to enable capture.
- Can listen to ACC broadcasting on loopback for competitor data when configured.
- Can inspect and create a copy of a setup JSON from a configured directory using a user-supplied profile explicitly marked validated. It backs up the source and does not load the result in ACC.

The model has six read-only race-data tools for tyre status, fuel projection, competitor gaps, recent incidents, weather and lap comparison. Deterministic trend events can flag changing gaps. Automatic follow-up listening, rolling incident buffers and race briefings are not implemented. Setup parameter mappings, conversions, event thresholds and in-game interpretations require external validation for each car/track. See [validation limits](docs/VALIDATION.md) and [ACC integration notes](docs/ACC_INTEGRATION.md).

## Provider credentials and radio

Configure an OpenRouter API key in the dashboard's AI settings. The key is stored with Windows Credential Manager through `keyring`, not in the SQLite settings database. Alternatively, set `OPENROUTER_API_KEY` in the process environment; environment credentials take precedence. Do not put credentials in project files or commit them.

The default text model is `deepseek/deepseek-v4.1-flash`. The default speech model is `fish-audio/drama-3`; select a Fish Audio voice in settings before requesting generated speech. These model identifiers and the speech endpoint were checked against the live OpenRouter model/endpoint catalogue during this release's preparation; availability, pricing and provider behavior can change. Requests are blocked when model pricing is unavailable or when the configured per-request/weekly local expenditure limits would be exceeded. Provider-reported billing is authoritative; local bounds are conservative estimates. See [provider details](docs/PROVIDERS.md).

For microphone push-to-talk, install the `radio` extra. For screenshot analysis, install `vision` with `python -m pip install -e ".[vision]"`. The first local faster-whisper transcription downloads the selected model (currently `base.en`) to the local model cache. Recording is explicit and bounded. Follow-up automatic listening is unavailable; the backend rejects settings that enable it, so use the configured push-to-talk control.

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The GitHub Actions workflow runs pytest on Windows with Python 3.11 and 3.13. The current suite has 58 passing tests. Tests exercise application behavior with fakes/synthetic data; they do not establish live ACC acceptance or performance.

To build an optional standalone folder distribution, run `.\Build-Engineer.ps1`. Launch `dist\ACC-Race-Engineer\ACC-Race-Engineer.exe`; it starts the local server and opens the dashboard. The Python launcher remains the recommended development path; the packaged build has not been validated on a clean Windows machine.

## Safety and limitations

The application does not write to ACC shared memory, modify ACC configuration, automatically rebind controls, or load a setup into the game. Broadcasting registration is read-only. Verify control conflicts manually. Treat all calculations and generated speech as assistance and confirm critical race decisions using ACC's own displays.
