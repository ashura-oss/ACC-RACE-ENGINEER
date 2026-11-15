# Engineer voice, memory and race behaviour

This note describes the implemented native engineer path. The voice and
strategy are designed to be concise and evidence-led; this does not establish
that warnings are race-ready. See [validation limits](VALIDATION.md).

## Driver profile and local memory

The **Personal engineer** page saves a bounded profile: driver name,
encouragement level (`none`, `brief` or `high`), an explicit opt-in for
occasional profanity, and supported driving preferences. The engineer uses the
profile in deterministic calls where relevant. Profanity such as “good shit”
after a verified win is permitted only when the driver explicitly enables it.

Profile, conversation exchanges and verified final-session summaries are
indexed in a persistent local SQLite FTS index. The index is reused after app
restarts and across races. It contains no raw radio audio. Session summaries
are indexed only when a session has a verified final state, and are limited to
session id, track, car, completed laps and final position. Assistant wording in
a stored conversation is labelled unverified; it is not promoted to telemetry.

The provider prompt receives a small memory manifest, not full conversations or
profile text. The model can call `search_driver_memory` when it needs a name,
preference or past note. Search returns selected excerpts capped at 360
characters apiece (up to eight excerpts), which are included in the following
provider turn. The current driver question and any relevant live race context
are also sent normally. Common credential-like patterns are redacted before
indexing, but the redaction is heuristic and cannot guarantee detection of
every secret. The SQLite database is local personal data, not a credential
vault. Raw microphone audio is not part of this search index.

Local endpoints are `GET`/`PUT /api/driver/profile`, `GET /api/driver/memory`
and `GET /api/strategy`; the native app uses these same interfaces.

## Spoken delivery

The default is Fish Official Adrian, pinned by a fixed voice ID. The same
selected voice is used for approved clips and generated calls unless the user
changes the voice setting. Fish Drama 3 speech includes a short inline bracketed
performance direction for the selected delivery, such as concerned, urgent,
excited or firm. Drama 3 documents inline plain-language performance cues at
[Fish Audio](https://fish.audio/drama-3/) and on its
[OpenRouter model page](https://openrouter.ai/fish-audio/drama-3). These cues
guide performance; event selection and emotion labels come from local runtime
logic.

Audio is rendered locally after generation or approval. The base speed is 1.18;
small emotion-specific changes use pitch-preserving tempo processing. The
default gritty radio effect adds a 350–3500 Hz voice band, light compression,
subtle saturation and quantization texture, plus a faint deterministic static
and squelch bed. `dry`, `light`, `gritty` and `strong` levels are available.
Rendered audio is cached separately from source clips using source audio,
selected voice, emotion, speed, effect and volume as cache inputs.

## Deterministic calls and strategy

The local director consumes fresh live ACC frames and deterministic events. It
handles critical flags and incidents, measured gaps, fuel conditions, lap
events, weather changes, penalties and selected temperature conditions. Stale,
paused, replay or frozen physics frames do not produce live director calls.
Approved critical clips are pre-rendered at startup. A priority-zero call uses
the local approved clip when available and can interrupt lower-priority queued
audio. The app surfaces missing critical recordings for review.

After a severe incident, the director can issue one delayed “How's the car
feeling?” check-in unless the driver has already responded. A relevant positive
acknowledgement clears the concern; it is not repeated as an endless prompt.
The driver can also opt into a five-second microphone window after the engineer
asks a spoken question. That window starts only after the audio message reports
successful completion and the output queue clears. The option defaults off;
there is no always-on microphone.

A win call requires an explicit finished race state and measured P1. A checkered
flag in practice does not qualify. The fuel plan uses measured fuel projection
and reserve inputs. Other measured actions cover supported tyre/brake
temperature thresholds and ACC's near-term rain reading. Missing or stale
inputs produce an unknown plan. There is no verified pit-loss model, optimal
undercut/overcut calculation, opponent stint model or automatic setup upload;
those decisions remain unknown.

## Limits of these behaviours

Local event logic and mock/offline integration tests exercise these paths, but
they do not prove live microphone timing, generated speech quality or warning
usefulness in a race. A previous two-lap ACC spot check matched one lap-time
reading with the HUD and observed fuel, speed and session-clock values; the
latest main-menu inspection had no available shared-memory mappings. There is
no complete live-race acceptance for the current build. See the scoped evidence
and outstanding validation in [VALIDATION.md](VALIDATION.md).
