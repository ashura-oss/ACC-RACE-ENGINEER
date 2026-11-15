# Validation status and limits

## Native Windows interface

Version 0.3 uses PySide6 Qt Widgets for its own desktop window, navigation, forms,
tables and audio review. It neither embeds a WebView nor launches a browser.
The native process owns a private ephemeral loopback backend with a per-instance
authentication token. Closing the window stops that backend and radio services.
The native pages include personal engineer preferences, current director state,
verified session summaries, and measured strategy actions. Offline Qt and
runtime-integration tests exercise those interfaces and their API contracts;
they do not constitute live-race or clean-machine acceptance.

## What is covered

The pytest suite covers native UI behavior, deterministic calculations,
application/API behavior with test fixtures, setup file safety, screenshot
guards, model-tool behavior, persistent local memory, runtime orchestration and
synthetic telemetry/protocol inputs. CI runs the suite on Windows with Python
3.11 and 3.13. This is software-level coverage only.

A PyInstaller Windows folder build was previously exercised through the legacy dashboard routes and passed start/stop API smoke checks (HTTP 200). That historical check does not validate the native window, current packaged UI, clean-machine installation, or game compatibility.

Windows keyring credential storage and OpenRouter authentication were smoke-checked successfully (HTTP 200); no credential value is included in this repository.

The ACC shared-memory reader follows the documented shared-memory v1.8.12 layout
and reads existing mappings without creating game pages. Broadcasting implements
protocol 4 and registers as a read-only client. Neither statement establishes
compatibility with a live game session.

## Limited ACC spot checks

A prior two-lap ACC run produced a 119,877 ms lap-time reading that matched the
in-game HUD, along with observed fuel, speed and session-clock readings. That
short run exposed a frozen physics page whose zero values could look live. The
reader now marks frozen physics data invalid, and synthetic tests cover its
rejection by live event and strategy paths. This is a regression-tested fix,
not a repeat live acceptance result.

At the latest inspection, the ACC process was at the main menu and its shared
memory mappings were unavailable. There is no current live telemetry sample and
no full-feature live-race acceptance for this build.

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

The race-data and measured-strategy tools are read-only and return unknown when fresh data is
unavailable. A separate `search_driver_memory` tool can retrieve a bounded local
memory excerpt when the model requests it; it does not turn stored history into
telemetry. Gap-trend notifications are deterministic events; they do not
establish a safe pass or predict future gaps. Follow-up listening is opt-in and
off by default; when enabled, it opens a single five-second listening window
only after an engineer question has finished playing. There is no always-on
microphone mode. Pit-loss timing, optimal undercut/overcut strategy, automatic
setup upload and a complete race briefing remain unimplemented. The complete
requested product remains the target in [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md);
this early release does not claim every V1 acceptance criterion is met.

## Speech repair validation

A live Fish Drama 3 default-voice request returned PCM at 44.1 kHz mono. It was wrapped into WAV using the response metadata. A normalized copy of the known generated phrase was correctly transcribed locally as Radio Check, Engineer online. The local base.en recognition model is cached. All 27 standard radio recordings were generated locally for driver review; none were automatically approved. These checks used generated speech, not live microphone input.

## Fixed engineer voice

The selected identity is Fish Official Adrian (bf322df2096a46f18c579d0baa36f41d). Two live speech requests with different text succeeded using that explicit ID. All 27 library scripts were regenerated with the same voice and speech model; previous versions were retained and deapproved. New versions require driver review. Cached playback and approval reject mismatched voice/model/script metadata, and missing critical recordings are shown in the native overview. No claim is made that natural speech prosody is identical across utterances.


## 2026-11-15 immersive engineer checks

The source suite passes 197 tests locally on Windows/Python 3.13, including
critical preemption with a missing clip, suppression-aware opt-in listening,
verified race wins, changing preferences, cross-restart local retrieval, and
pitch-preserving stereo audio. Luna coding agents implemented components and a
Terra low review found the critical integration issues that were then corrected.
The reviewer did not independently rerun the suite.

A live GPT-6 Luna call requested the local memory search tool and recalled the
driver's saved gritty/brisk radio preference. The initial context supplied a
manifest rather than history; only tool results were sent after retrieval.
The first live check exposed overly strict lexical matching, which was repaired
and checked again. This is bounded lexical retrieval, not a local LLM or vector
embedding service.

Thirty current-speaker library scripts are present and approved under the
user's explicit approval. Critical and expressive example lines were regenerated
with Drama 3 inline emotion directions. A generated concern/best-lap/win preview
was successfully transcribed by cached local base.en; this verifies intelligible
generated audio, not live microphone capture or subjective emotional quality.
The default local radio render uses a 1.18 tempo multiplier with pitch retained.

No fresh shared-memory mapping was available during the latest inspection.
The 197 tests and cloud checks do not constitute a complete in-race acceptance,
a latency guarantee, a clean-machine installer check, or confirmation that the
driver likes the resulting voice.


The Windows suite initially reported passing tests but aborted during Qt teardown.
The cause was test modules independently owning/releasing QApplication. A shared
session fixture and explicit widget disposal resolved it: the final full-suite
run reported 197 passed and the interpreter exited normally with code 0.
The packaged app was also opened, the fixed speaker's voice test reached playback,
and local base.en preparation reported Ready without opening a microphone. The
Personal engineer page received a scrolling/minimum-size layout fix after native
inspection exposed compressed fields at the default window size.

Repeated paused ACC frames now invalidate old race audio once, while allowing
new general radio conversations. This has an offline integration regression.
