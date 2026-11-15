# ACC integration and validation

The shared-memory layout uses the Kunos ACC shared-memory v1.8.12 ABI, 4-byte
packing, fixed-width 32-bit numbers and 16-bit Windows UTF-16 code units.
Only existing read-only mappings are opened; the app never creates game pages.
Physics and graphics are copied with packet checks independently; they are not
an atomic cross-page snapshot. Source timestamps advance only when packet IDs
change. After two seconds without updates, the sample is marked stale.

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

No live ACC race or attached microphone/controller was available during adapter
development. Unit tests exercise ABI offsets and synthetic packets, not ACC
compatibility. Before racing, verify fuel units, session clock conversion,
flags, pressure/temperature values, lap transitions and gaps against the HUD.
Test paused/replay/disconnected behavior, registration/password failure, driver
swaps, damage events and R3 press-release sequences with ACC running.
Every flag/event category and crash threshold needs recorded-race validation.
No actual game-performance or warning-latency guarantee has been established.
