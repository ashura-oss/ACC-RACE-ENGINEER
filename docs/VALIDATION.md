# Validation status and limits

## Native Windows interface

Version 0.2 uses PySide6 Qt Widgets for its own desktop window, navigation, forms,
tables and audio review. It neither embeds a WebView nor launches a browser.
The native process owns a private ephemeral loopback backend with a per-instance
authentication token. Closing the window stops that backend and radio services.
The native tests cover page construction, live status, controls, library layout,
credential clearing, authenticated requests and real backend startup/shutdown.
The complete local suite currently passes 66 tests. The source application was
also checked interactively for start, stop, library navigation and clean close.

## What is covered

The current pytest suite has 66 passing tests covering native UI behavior, deterministic calculations, application/API behavior with test fixtures, setup file safety, screenshot-capture guards, model-tool behavior and synthetic telemetry/protocol inputs. CI runs the suite on Windows with Python 3.11 and 3.13. This is software-level coverage only.

A PyInstaller Windows folder build was previously exercised through the legacy dashboard routes and passed start/stop API smoke checks (HTTP 200). That historical check does not validate the native window, current packaged UI, clean-machine installation, or game compatibility.

Windows keyring credential storage and OpenRouter authentication were smoke-checked successfully (HTTP 200); no credential value is included in this repository.

The ACC shared-memory reader follows the documented shared-memory v1.8.12 layout and reads existing mappings without creating game pages. Broadcasting implements protocol 4 and registers as a read-only client. Neither statement establishes compatibility with a live game session.

## Not yet validated in a live environment

The application is now a native Qt desktop app with a private ephemeral loopback backend. The native pages and packaged-window behavior still require direct Windows acceptance; a passing API/unit suite is not evidence that the native UI works correctly. Screenshot analysis is implemented as an explicit request. The capture helper only attempts the visible ACC client area when ACC is foreground and the client region lies on one primary monitor. It captures one frame, does not persist it, and submits it to the selected vision-capable model only as part of the explicit request. These guardrails have software tests; actual capture compatibility remains hardware-dependent.

No live ACC race, attached microphone or controller acceptance session is recorded for this release. In particular, the following remain unverified:

- Native page behavior, UI responsiveness, shutdown/restart behavior, and packaged launch on a clean Windows machine.
- Live shared-memory values and units across cars, sessions, replays, pause/resume and game updates.
- Flags, pit state, damage signals, tyre/brake temperatures, lap boundaries, session clock, and competitor gaps against the in-game HUD.
- Broadcasting registration, password behavior, packet timing, driver swaps, and reconnects against a running ACC instance.
- R3/keyboard behavior and conflicts with game controls on actual hardware.
- Microphone device compatibility, speech recognition quality, generated speech playback and end-to-end radio latency.
- Event thresholds, warning usefulness, fuel projection assumptions, setup parameter mappings and resulting on-track behavior.
- Screenshot capture on supported real-world display configurations, and the accuracy or usefulness of visual analysis.
- Performance, race suitability, or any warning-latency guarantee.

## Before relying on it

Use a test session and compare every displayed measurement with ACC. Check connected/disconnected, replay, pause, session transitions, stale data, broadcasting authentication and controller press/release behavior. Test event thresholds against recorded sessions, validate each setup profile against the correct car and setup format, and load setup copies manually while stationary. Do not infer that passing unit tests means race acceptance has passed.

The calculations deliberately preserve uncertainty: stale or missing telemetry is treated as unknown, and the fuel estimate is based on measured laps plus a finishing-lap reserve assumption. Provider output can still be wrong; independently confirm important advice.

The six model tools are read-only and return unknown when fresh data is unavailable. Gap-trend notifications are deterministic events; they do not establish a safe pass or predict future gaps. Rolling incident buffers, automatic follow-up listening and race briefings are not implemented. Follow-up listening is explicitly rejected by settings validation. The complete requested product remains the target in [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md); this early release does not claim every V1 acceptance criterion is met.
