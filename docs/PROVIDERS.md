# Provider and credential behavior

## OpenRouter

OpenRouter is optional. It provides the model catalogue, text completions and generated speech. The current defaults are:

| Use | Model identifier | Endpoint |
| --- | --- | --- |
| Radio text | `deepseek/deepseek-v4.1-flash` | `/chat/completions` |
| Generated speech | `fish-audio/drama-3` | `/audio/speech` |

The speech model is resolved through OpenRouter's model endpoint catalogue (`/models/{model}/endpoints`) when it is not in the normal text catalogue. A Fish Audio voice must be selected in the dashboard before speech can be generated. These identifiers/endpoints were verified against the live OpenRouter catalogue for this release; availability and pricing may change. The app refreshes catalogue/pricing when needed and blocks requests if pricing data is missing.

## Credentials

The dashboard saves an OpenRouter key to the Windows credential store via Python `keyring` under service `ACC AI Race Engineer`, account `openrouter`. The key is not stored in the app's SQLite database. `OPENROUTER_API_KEY`, when present in the process environment, takes precedence over the saved key. Do not store keys in source files, screenshots, issue reports or version control.

## Spend controls and logging

Settings include a weekly local budget and a per-request ceiling. Before a request, the client estimates an upper bound from the model's listed pricing, request size and completion cap. If pricing is absent or the estimated request would cross either limit, the request is blocked. When OpenRouter returns usage cost, that value is recorded; otherwise the estimate is recorded. OpenRouter's billed amount is authoritative, so the local estimate is not a billing guarantee.

The local database records model, cost or estimate, token count when available, and latency. Provider error bodies are not persisted because they may contain sensitive material. Text answers use supplied race context and can be suppressed from playback if the session state changes while the request is running. Speech generation failure leaves the response text available.

## Network and privacy

The dashboard server binds to `127.0.0.1`. Provider requests require network access and send the driver prompt plus currently usable race context to OpenRouter. Telemetry is not sent automatically as a background stream; it is included only in a radio request. Local faster-whisper is optional and downloads its model on first transcription. See [validation limits](VALIDATION.md) before using radio output in a race.
