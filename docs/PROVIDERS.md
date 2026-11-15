# Provider and credential behavior

## OpenRouter

OpenRouter is optional. It provides the model catalogue, text completions and generated speech. The current defaults are:

| Use | Model identifier | Endpoint |
| --- | --- | --- |
| Radio text | `openai/gpt-6-luna` | `/chat/completions` |
| Generated speech | `fish-audio/drama-3` | `/audio/speech` |

The speech model is resolved through OpenRouter's model endpoint catalogue (`/models/{model}/endpoints`) when it is not in the normal text catalogue. Every speech request explicitly pins a voice ID. The default is Fish Official Adrian (`bf322df2096a46f18c579d0baa36f41d`); a blank field resolves to that same fixed ID. The app never silently falls back to an unselected provider voice. Cached recordings must match the selected voice, speech model and script before approval/playback; legacy versions remain preserved but inactive. The app requests PCM and wraps it into WAV using the sample rate and channels returned in the Content-Type header; it does not guess the sample rate. These identifiers/endpoints were verified against the live OpenRouter catalogue for this release; availability and pricing may change.

AI settings includes **Test saved voice** and **Prepare speech recognition**. Save edited settings before testing voice. Preparation downloads/loads the local recognition model without opening the microphone. The recorder supplies PCM16 mono 16 kHz samples directly to faster-whisper as a normalized NumPy array, avoiding the incompatible AV decoder path. Models are cached under the application data directory.

## Credentials

The dashboard saves an OpenRouter key to the Windows credential store via Python `keyring` under service `ACC AI Race Engineer`, account `openrouter`. The key is not stored in the app's SQLite database. `OPENROUTER_API_KEY`, when present in the process environment, takes precedence over the saved key. Do not store keys in source files, screenshots, issue reports or version control.

## Spend controls and logging

Settings include a weekly local budget and a per-request ceiling. Before dispatch, the client estimates an upper bound from the model's listed pricing, request size and completion cap, then writes a reservation. Reservations remain in weekly spend even if a request fails or is cancelled, because it might still be billable. If pricing is absent or the estimated request would cross either limit, the request is blocked. When a response includes provider usage, that report is saved separately from the reservation.

`GET /api/usage` returns both `reservations` (the conservative amounts used for local budget gating) and `provider_reports` (provider-reported usage/cost where available, otherwise a marked estimate). The dashboard's weekly spend gate is based on reservations, not a claim of actual billed spend. OpenRouter's account billing remains authoritative.

The local database records model, reserved amount, outcome, and a separate provider usage report with token count when available and latency. Provider error bodies are not persisted because they may contain sensitive material. Text answers use supplied race context and six read-only tools for fresh tyre status, fuel projection, competitor gaps, recent incidents, weather and lap comparison. Tool results are model requests against a context snapshot; unavailable or stale data returns unknown. Speech generation failure leaves the response text available.

## Network and privacy

The dashboard server binds to `127.0.0.1`. Provider requests require network access and send the driver prompt plus currently usable race context to OpenRouter. Telemetry is not sent automatically as a background stream; it is included only in a radio request. An explicitly requested screenshot analysis captures one foreground ACC client image on the primary monitor and includes it in that provider request; the app does not persist the screenshot. Local faster-whisper is optional and downloads its model on first transcription. See [validation limits](VALIDATION.md) before using radio output in a race.
