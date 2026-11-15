# ACC integration and validation

The shared-memory layout uses the Kunos ACC shared-memory v1.8.12 ABI, 4-byte
packing, fixed-width 32-bit numbers and 16-bit Windows UTF-16 code units.
Only existing read-only mappings are opened; the app never creates game pages.
Physics and graphics are copied with packet checks independently; they are not
an atomic cross-page snapshot. The optional static page reads only its first
200 bytes (`smVersion`, `acVersion`, session/car counts, car model and track).
Missing static metadata does not disable live telemetry. Car and track identity
is trimmed, case-folded and normalized to underscores. Source timestamps
advance only when their packet IDs change. After two seconds without updates to
either page, the sample is marked stale.

Physics and graphics packet counters have independent freshness timestamps.
The combined `timestamp` uses the older page, and the frame becomes stale when
either page stops advancing. `physics_valid` is false when the physics page is
stale or every checked car signal is zero; physics measurements then become
`null`. An actual zero fuel reading remains available when another car signal
confirms a populated physics page.

The SDK documents graphics `sessionTimeLeft` as a float but does not specify its
unit. A live 10-minute Quick Race returned `600000`, matching milliseconds, so
the adapter divides by 1000 for its `session_time_left_s` consumer. The
`iCurrentTime`, `iLastTime`, and `iBestTime` integer fields are milliseconds.
ACC uses `2147483647` as the unavailable last/best lap sentinel; those values
are returned as `null` so an uninitialized session cannot look like a lap record.
`isValidLap` is exposed as `lap_valid` and its inverse as `invalid_lap`. Pit box
and pit lane state are kept separate (`in_pit` and `in_pit_lane`). Global flags
are exposed from the documented global and sector fields. Session names follow
the ACC session enum. Pit-window timing is not exposed by these graphics pages;
it requires the separate static page, which this adapter does not currently map.

Reference documentation: Kunos' `ACCSharedMemoryDocumentationV1.8.12.pdf`,
distributed with the ACC SDK (a public documentation mirror is at
https://github.com/SolusDev-sys/CppAccSharedMemory/blob/main/ACCSharedMemoryDocumentationV1.8.12.pdf).
Broadcast protocol field order follows Kunos `ksBroadcastingNetwork` SDK,
distributed under ACC Dedicated Server `sdk/broadcasting/Sources`.
The adapter uses protocol 4, length-prefixed UTF-8, little-endian numbers and
read-only registration (no command password). Car updates preserve raw gear;
no gear conversion is inferred from third-party examples.

Enable broadcasting in your Documents `Assetto Corsa Competizione/Config/broadcasting.json`:

```json
{"udpListenerPort":9000,"connectionPassword":"choose-a-local-password","commandPassword":""}
```

Restart ACC after changing its configuration. Configure the same connection
password in the dashboard. The application only connects to loopback. It does
not change ACC files, mute ACC's engineer, or rebind game controls automatically.
Remove ACC's R3 assignment manually if it conflicts with the radio toggle.

Xbox controls use XInput at 100Hz with rising-edge detection and 180ms debounce.
The default button is R3; keyboard alternatives use Windows virtual-key codes.
The optional local microphone service requires `sounddevice`; CPU transcription
requires `faster-whisper` and downloads its selected model on first use.
Recording is explicit and bounded to 30 seconds. Playback must be paused during
recording; closed-back headphones are recommended.

## Required live-game checks

A live Quick Race verified read-only attachment, changing speed/RPM/fuel values,
race session type, and the 10-minute clock conversion. Unit tests exercise ABI
offsets and synthetic packets; they do not validate every field against the
HUD. Fuel, flags, pressure/temperature values, lap transitions and gaps still
need HUD comparison. Paused/replay/disconnected behavior, registration/password
failure, driver swaps, damage events and R3 press-release sequences also need
live validation.
Every flag/event category and crash threshold needs recorded-race validation.
No actual game-performance or warning-latency guarantee has been established.
