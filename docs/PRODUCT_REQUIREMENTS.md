# ACC AI Race Engineer
## Final Product Requirements & Technical Specification — V1.0

**Status:** Finalised design specification — development not yet started  
**Platform:** Windows 10/11  
**Simulator:** Assetto Corsa Competizione (ACC)  
**AI provider:** OpenRouter  
**Reasoning model:** DeepSeek (working selection: V4.1 Flash)  
**Voice generation:** Fish Audio Drama 3  
**Backend:** Python + FastAPI  
**Target usage:** Approximately 10 racing hours weekly, primarily 15-minute races

---

# 1. Product Vision

Build a fully conversational, proactive and emotionally realistic AI race engineer that replaces the standard ACC radio engineer.

The system must:

- Monitor live race telemetry and competitor information.
- Deliver routine race announcements without requiring user input.
- Identify important changes and proactively communicate them.
- Answer questions naturally using live race data.
- Remember conversations, incidents, strategies and previous laps.
- React emotionally and appropriately to race developments.
- Provide technical advice regarding tyres, fuel, damage and race performance.
- Generate and manage pre-race vehicle setup files.
- Examine selected gameplay screenshots when additional visual context is useful.
- Operate through an Xbox controller without requiring keyboard interaction.
- Provide a comprehensive local dashboard for configuration, audio management, monitoring and debugging.

**The desired experience is a professional race engineer who genuinely feels involved in the race — not a generic AI assistant or telemetry reader.**

---

# 2. Final System Architecture

```text
         ASSETTO CORSA COMPETIZIONE
                  Windows PC
                      |
          +-----------+-----------+
          |                       |
     Shared Memory          Broadcasting
       Telemetry                 API
          |                       |
          +-----------+-----------+
                      |
               PYTHON BACKEND
                 (FastAPI)
                      |
       +--------------+---------------+
       |              |               |
   Event Engine   Race Memory    Screen Capture
       |              |               |
       |              +-------+-------+
       |                      |
       |               DEEPSEEK API
       |               via OpenRouter
       |                      |
       |               Response Text
       |                      |
       |                 FISH AUDIO
       |               via OpenRouter
       |                      |
       +----------+-----------+
                  |
            Audio Scheduler
                  |
             YOUR HEADSET


  XBOX CONTROLLER ---> RADIO INPUT SYSTEM
                            |
                      Speech-to-Text
                            |
                       DEEPSEEK


  LOCAL DASHBOARD
        |
        +-- Start / Stop Engineer
        +-- Keybind Configuration
        +-- Audio Library
        +-- Telemetry Monitoring
        +-- AI Activity & Costs
        +-- Event Logs
        +-- Setup Management
        +-- Testing & Debugging
```

All orchestration runs on the gaming desktop.

The separate RTX 4050 laptop and Ethernet switch are not required for V1.

---

# 3. Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Game integration | ACC Shared Memory API | Read car and session telemetry |
| Competitor monitoring | ACC Broadcasting API | Read driver information, timing and race events |
| Backend | Python 3.x | Application logic |
| Web framework | FastAPI | Dashboard services and internal APIs |
| AI reasoning | DeepSeek via OpenRouter | Interpret data and generate dialogue |
| Voice synthesis | Fish Audio Drama 3 | Expressive engineer voice |
| Speech recognition | To be selected | Transcribe radio input |
| Controller detection | Windows-compatible gamepad input library | Read Xbox controller buttons |
| Screen capture | DXcam or Windows Graphics Capture | Capture gameplay frames |
| Database | SQLite | Race history, configuration and logs |
| Audio playback | Local Python audio service | Playback, interruption and priority |
| Setup management | Python JSON processing | Modify saved ACC setup files |
| Dashboard | Web interface | Control and monitor the application |

**Important:** The exact DeepSeek model identifier, multimodal support, function-calling behaviour and Fish Audio integration must be verified against the live OpenRouter catalogue before implementation. They are selected technologies, not yet tested integrations.

---

# 4. Xbox Controller Radio System

## 4.1 Default controls

**Primary radio button: Xbox right-stick click (R3/RS).**

Radio operation uses toggle mode.

| Action | Result |
|---|---|
| First R3 press | Start recording your microphone |
| Second R3 press | Stop recording and submit your message |
| Speech recognition completes | Send transcript and race context to DeepSeek |
| DeepSeek responds | Generate speech using Fish Audio |
| Audio becomes available | Play through your headset |

There is no requirement to hold the button.

There is no wake word.

No keyboard interaction should be necessary during normal racing.

## 4.2 Additional requirements

- R3 is configurable in the dashboard.
- Support keyboard and alternative controller bindings.
- Detect conflicting ACC keybinds where possible.
- Display microphone state in the dashboard.
- Provide an optional radio activation sound.
- Allow cancellation of accidental recordings.
- Prevent double presses from generating duplicate requests.
- Support interruptions if an urgent warning occurs.
- Prevent duplicate input events while a button remains depressed.

## 4.3 Conversational follow-ups

If the engineer asks a question, such as:

"How's the steering feeling?"

The application may temporarily enter a follow-up listening state.

This must be configurable, and the user should know when the microphone is active.

Normal radio conversations otherwise use R3 activation.

---

# 5. Engineer Personality

The engineer should have a consistent, believable motorsport personality.

### Personality characteristics

- Professional and technically competent.
- Competitive and invested in race results.
- Calm during normal driving.
- Urgent when safety or race position demands it.
- Occasionally irritated by unnecessary mistakes.
- Supportive after serious incidents.
- Capable of dry humour.
- Excited when a significant achievement genuinely warrants it.
- Willing to challenge poor decisions.
- Never excessively enthusiastic, flattering or artificially cheerful.

### Examples

**Routine lap improvement**

"Good lap. That's two tenths quicker. Let's keep that consistency."

**Avoidable mistake**

"Oh, come on, mate. We didn't need that. Right, let's focus and get ourselves back into this."

**Heavy crash**

"That's a big hit. You alright? Talk to me."

**Race victory**

"YES! That's P1! Brilliant drive. Absolutely brilliant. Well done, mate."

### Personality logic

The backend maintains a compact race-state summary containing:

- Current race situation.
- Recent incidents.
- Performance trends.
- Previous engineer recommendations.
- Driver responses.
- Recent emotional context.

DeepSeek uses these facts to select appropriate dialogue.

The engineer must never invent race information simply to make a conversation more dramatic.

---

# 6. Standard Race Radio Replacement

Our engineer should reproduce as much of ACC's existing race-radio functionality as its accessible telemetry allows.

## 6.1 Routine announcements

Target event categories include:

- Green flags.
- Yellow flags.
- Blue flags.
- Race starts.
- Session changes.
- Lap completion.
- Personal-best laps.
- Session-best laps.
- Penalties.
- Invalid laps.
- Pit-window information.
- Fuel warnings.
- Damage warnings.
- Race completion.

The existing ACC engineer should be muted where the game allows it, preventing overlapping voices.

The event engine will use documented telemetry and broadcasting fields.

**Validation requirement:** Every event category must be tested against ACC. Some events may require derived logic, and complete parity with the game's built-in engineer is not guaranteed.

---

# 7. Pre-Recorded Radio Audio Library

## 7.1 Initial setup requirement

During first-run configuration, the system generates the complete standard radio library using Fish Audio.

All generated audio is saved locally.

The driver must be able to review recordings before using them in a race.

## 7.2 Audio categories

- Flags and track warnings.
- Penalties and track limits.
- Lap records.
- Pit instructions.
- Fuel warnings.
- Damage alerts.
- Driver check-ins.
- Race starts.
- Race finishes.
- Standard acknowledgements.
- Common engineering instructions.

Some lines should have multiple emotional versions.

For example:

**Normal:** "Box this lap."

**Urgent:** "Box, box, box!"

## 7.3 Audio management

The dashboard must allow users to:

- Play any generated audio.
- View its associated text.
- Edit the dialogue.
- Regenerate the recording.
- Approve or reject a recording.
- Replace a recording.
- Delete unwanted audio.
- Browse previous generated versions.
- Organise clips by category.
- Generate missing recordings.
- Regenerate the complete library when changing voices.

## 7.4 Playback priority

| Priority | Type | Behaviour |
|---|---|---|
| P0 | Critical safety warnings | Interrupt current speech immediately |
| P1 | Important race events | Play as soon as practical |
| P2 | Driver-requested responses | Play conversationally |
| P3 | Non-urgent proactive commentary | Delay or suppress when necessary |

Critical warnings must bypass DeepSeek and dynamic speech generation.

---

# 8. Proactive Race Intelligence

The backend continuously analyses the race without continuously calling DeepSeek.

A deterministic event engine detects meaningful changes.

Potential triggers include:

- A competitor rapidly closing the gap.
- The driver gaining on another car.
- Deteriorating tyre conditions.
- Increasing brake temperatures.
- Low projected fuel.
- Weather changes.
- Damage after contact.
- Significant lap-time improvements.
- Consecutive poor laps.
- Pit-window changes.
- Important race-position changes.

For example:

"Car behind's closing. Gap's down to 1.2 seconds. He's gaining in the middle sector."

The engineer should avoid repeated announcements about unchanged conditions.

Each proactive event requires configurable thresholds, cooldowns and priority rules.

---

# 9. Live Race Telemetry

## 9.1 Vehicle monitoring

Read available data including:

- Speed and engine RPM.
- Gear.
- Fuel remaining.
- Fuel consumption.
- Tyre pressures.
- Tyre temperatures.
- Brake temperatures.
- Brake condition.
- Vehicle damage.
- Suspension-related data.
- Current lap time.
- Previous lap times.
- Best lap.
- Relevant driving-assistance settings.

## 9.2 Race and competitor information

Use available broadcasting data to monitor:

- Driver names.
- Car numbers.
- Race positions.
- Lap records.
- Competitor timing.
- Track position.
- Pit activity.
- Broadcasting event notifications.

## 9.3 Session conditions

Monitor available:

- Track temperature.
- Ambient temperature.
- Rain intensity.
- Weather forecasts.
- Track condition.
- Session time remaining.
- Session phase.
- Relevant flag conditions.

The telemetry service must preserve timestamps and detect stale or missing data.

---

# 10. Crash Detection and Driver Check-Ins

## 10.1 Crash detection

The backend monitors sudden changes in damage and other indicators consistent with contact.

A collision is classified using available evidence rather than a single arbitrary threshold.

## 10.2 Incident severity

Three initial categories:

**Minor:** Small damage increase. Brief acknowledgement or no interruption.

**Moderate:** Noticeable damage. Ask about handling and suggest monitoring the car.

**Severe:** Significant damage. Immediately check on the driver and recommend assessing whether to continue or pit.

Severity thresholds must be validated against recorded races.

## 10.3 Driver conversation

Example:

**Engineer:** "That's a big one! You alright, mate?"

**Driver:** "Yeah, I'm okay."

**Engineer:** "Good. How's the car feeling? We've picked up some front-end damage."

**Driver:** "Steering feels alright."

**Engineer:** "Copy. Let's keep an eye on it for the next few corners."

## 10.4 No-response handling

If the driver fails to answer:

- Wait an appropriate interval.
- Repeat the check-in once.
- Avoid repeatedly distracting the driver.
- Preserve the unresolved incident in memory.

Game telemetry cannot establish the driver's real-world medical condition.

## 10.5 Follow-up memory

The engineer should remember the crash and potentially ask about it later if relevant.

"How's the front end holding up? Still feeling alright?"

---

# 11. Natural Conversational Intelligence

The engineer must answer natural questions using verified race data.

Examples:

- "What's my gap to the car ahead?"
- "How are the tyres?"
- "Do we have enough fuel?"
- "Who's behind me?"
- "What's happening with the weather?"
- "How much time have we got left?"
- "Should I pit?"
- "What happened to that car?"
- "How was my last lap?"
- "Why am I losing time?"

## 11.1 AI context

Each request should contain only the information relevant to the question:

- Current telemetry.
- Relevant competitor data.
- Recent event history.
- Selected race memory.
- Driver's latest message.
- Current engineer conversational state.

This reduces latency and API cost.

## 11.2 Function execution

DeepSeek may request backend functions such as:

- `get_tyre_status()`
- `get_fuel_projection()`
- `get_competitor_gaps()`
- `get_recent_incidents()`
- `get_weather_forecast()`
- `get_lap_comparison()`
- `prepare_setup_file()`

The backend supplies actual measurements or calculated results.

The model must not fabricate values.

---

# 12. Visual Race Understanding

Gameplay screenshots supplement telemetry when needed.

## 12.1 Screen capture

The Windows application captures selected game frames.

A rolling local buffer may retain recent footage for incident investigation.

## 12.2 Trigger conditions

Screenshot analysis may occur:

- Following an accident.
- When the driver asks about a visible incident.
- When telemetry indicates unusual activity.
- During explicitly requested visual analysis.

## 12.3 Constraints

The model must distinguish:

- Visually confirmed observations.
- Telemetry-confirmed facts.
- Plausible interpretations.
- Unknown information.

For example, the engineer may say that two cars appear to be involved in an incident, but must not assert that one hit the other without sufficient evidence.

**Technical dependency:** The selected DeepSeek endpoint must be verified to support image inputs. Otherwise, image understanding requires a compatible alternative model.

---

# 13. Fuel Management

Designed primarily for short 15-minute races.

## 13.1 Pre-race calculation

Calculate recommended starting fuel using:

- Historical consumption per lap.
- Expected lap time.
- Race duration.
- Race-ending rules.
- Maximum plausible lap count.
- Configurable safety reserve.

## 13.2 During-race monitoring

Continuously recalculate:

- Average fuel used per lap.
- Estimated remaining laps.
- Fuel required to finish.
- Expected surplus or deficit.
- Potential fuel-saving requirements.

## 13.3 Proactive warnings

Example:

"Fuel's looking tight. We're projecting about a litre short at this pace. Let's start saving."

Fuel estimates must account for uncertainty in actual consumption and remaining laps.

---

# 14. Pre-Race Setup Management

We have selected **saved ACC setup-file editing** rather than mouse-based setup-menu automation.

## 14.1 Workflow

1. Driver requests a setup change.
2. DeepSeek interprets the request.
3. Python validates the parameter.
4. Backend copies an existing ACC-generated setup.
5. Requested values are modified.
6. Original setup is preserved.
7. Updated JSON is saved under a new name.
8. Driver loads the setup in ACC.
9. The system verifies relevant active settings where telemetry permits.

## 14.2 Target settings

- Starting fuel.
- Tyre pressures.
- Brake bias.
- Aerodynamic configuration.
- Suspension parameters.
- Traction-control and ABS setup values.
- Other validated saved-setup parameters.

Exact editable fields, units and game behaviour must be validated using real ACC-generated setup files.

**Important limitation:** Editing a file does not automatically load it into the active car.

Automatic setup loading is outside the confirmed V1 scope.

---

# 15. Local Developer Dashboard

The dashboard is a required V1 feature.

Default address:

`http://localhost:8000`

## 15.1 Main pages

### A. Overview

- Start engineer.
- Stop engineer.
- Mute engineer.
- View operational status.
- View current ACC connection.
- View active model and voice provider.
- Monitor microphone state.
- Display API connectivity and errors.

### B. Radio Library

- List every pre-recorded clip.
- Play recordings.
- Review scripts.
- Generate new clips.
- Regenerate recordings.
- Approve or reject clips.
- Organise by category.
- Inspect generation costs.

### C. Live Telemetry

- Fuel.
- Tyres.
- Brakes.
- Damage.
- Lap times.
- Session status.
- Flags.
- Competitor information.

### D. AI Activity

- Recent requests.
- Responses.
- Tool calls.
- Relevant context supplied.
- API response times.
- Speech generation times.
- Token usage.
- Estimated costs.
- Errors and retries.

### E. Event Logs

- Event timestamps.
- Raw source event.
- Derived event classification.
- Triggered rule.
- Generated dialogue.
- Played audio file.
- Playback outcome.

### F. Controller & Keybinds

- Assign Xbox buttons.
- Configure R3 radio toggle.
- Detect duplicate assignments.
- Configure keyboard alternatives.
- Test controller connectivity.
- Test microphone and speaker devices.

### G. AI Settings

- OpenRouter API key.
- DeepSeek model selection.
- Fish Audio voice selection.
- Engineer personality parameters.
- Commentary frequency.
- Speech speed and volume.
- Conversation timeouts.
- Maximum API expenditure.

### H. Setup Manager

- Browse saved setups.
- Create modified setups.
- Compare changes.
- Maintain backups.
- Review selected car and track.
- Validate supported parameters.

### I. Debugging and Replay

- Simulate yellow flags.
- Simulate damage events.
- Test radio announcements.
- Replay recorded telemetry.
- Inspect what caused each message.
- Benchmark AI latency.

## 15.2 Persistence

Settings and logs are stored locally.

Audio files remain accessible after application restarts.

API credentials must be stored securely and must not appear in ordinary debug logs.

---

# 16. Audio System

The audio scheduler is responsible for coordinating all radio playback.

Requirements:

- Avoid overlapping radio messages.
- Interrupt lower-priority speech for critical warnings.
- Queue relevant messages.
- Discard outdated commentary.
- Support volume control.
- Support radio-style sound processing.
- Prevent the engineer's generated voice from feeding back into speech recognition.
- Log audio playback and interruptions.
- Cache reusable generated speech.

If an AI response becomes irrelevant before playback, the scheduler should suppress it.

---

# 17. Race Memory

SQLite stores relevant race history.

Potential records include:

- Session information.
- Lap times.
- Fuel consumption.
- Damage incidents.
- Opponent trends.
- Engineer recommendations.
- Driver decisions.
- Conversations.
- Radio messages.
- Weather changes.
- Pit activity.

The engineer should use this information to provide meaningful continuity.

Example:

"That rear-left's been running hot since lap four. We need to manage it."

Memory must remain concise enough to avoid unnecessary AI context and cost.

---

# 18. Reliability and Safety

## 18.1 Source reliability

Engineering calculations should be performed in Python.

The language model interprets results but does not invent measurements.

## 18.2 Failure handling

If DeepSeek or OpenRouter is unavailable:

- Continue local telemetry collection.
- Continue deterministic event detection.
- Play cached critical warnings.
- Disable unavailable conversational features gracefully.
- Record errors in the dashboard.

## 18.3 Configuration changes

Setup files are backed up before editing.

Unsupported parameters must be rejected rather than guessed.

## 18.4 Voice commands

Any action changing game settings must pass validation.

The engineer must not claim an action succeeded until the relevant outcome is verified.

---

# 19. Performance Targets

| Metric | Initial target |
|---|---|
| Critical local warning latency | Under 200 ms |
| Conversational response to first audio | Approximately 1–2 seconds |
| Radio-button detection | Under 50 ms |
| Dashboard response | Under 200 ms for normal local interactions |
| Telemetry freshness | Source-dependent, monitored continuously |
| Game performance impact | Minimal and benchmarked |
| Audio reliability | No overlapping critical messages |

These are development targets, not measured guarantees.

Network latency, speech transcription, DeepSeek inference and Fish Audio synthesis must be benchmarked end-to-end.

---

# 20. Cost Management

Primary recurring services:

1. DeepSeek inference through OpenRouter.
2. Fish Audio speech generation through OpenRouter.
3. Speech-to-text service, once selected.

Cost-saving methods:

- Pre-generate routine radio messages.
- Cache frequently reused dynamic messages.
- Avoid unnecessary AI calls.
- Filter telemetry before sending it to the model.
- Limit screenshots.
- Keep AI responses concise.
- Maintain a weekly expenditure budget.
- Track actual cost in the dashboard.

The system should provide configurable spending alerts and a maximum usage budget.

The exact running cost will be recalculated once live model prices and real speech volumes are measured.

---

# 21. Development Roadmap

## Phase 1 — Foundation

- Python backend.
- FastAPI dashboard.
- Configuration storage.
- ACC shared-memory reader.
- Broadcasting connection.
- Event logs.
- Connection diagnostics.

## Phase 2 — Audio System

- Fish Audio integration.
- First-run voice wizard.
- Pre-recorded radio library.
- Audio approval interface.
- Priority playback engine.
- Critical-warning event rules.

## Phase 3 — Controller and Conversation

- Xbox R3 toggle detection.
- Speech recognition integration.
- DeepSeek integration.
- Telemetry tool functions.
- Conversational audio responses.
- Conversation-state management.

## Phase 4 — Proactive Race Engineering

- Competitor gap analysis.
- Fuel projections.
- Tyre and brake monitoring.
- Proactive commentary.
- Crash response.
- Driver check-ins.
- Personality and race memory.

## Phase 5 — Pre-Race Engineering

- Fuel recommendations.
- Setup JSON management.
- Setup backups.
- Parameter validation.
- Active-setting verification where supported.

## Phase 6 — Advanced Intelligence

- Screen capture.
- Visual incident analysis.
- Rolling incident buffer.
- Race briefings and debriefings.
- More advanced contextual dialogue.

## Phase 7 — Reliability and Optimisation

- End-to-end latency testing.
- API cost benchmarking.
- Extensive race-event testing.
- Controller conflict testing.
- Telemetry replay testing.
- Error recovery.
- Packaging into a Windows application.

---

# 22. V1 Acceptance Criteria

V1 will be considered functional when:

- [ ] The application connects to ACC and reads live telemetry.
- [ ] The dashboard can start and stop the engineer.
- [ ] Xbox R3 toggles microphone recording on the first and second press.
- [ ] Speech is transcribed and sent to DeepSeek.
- [ ] DeepSeek can answer questions using actual race data.
- [ ] Fish Audio generates audible engineer responses.
- [ ] The initial audio setup wizard works.
- [ ] Pre-recorded messages can be reviewed and approved.
- [ ] Critical warnings play without AI inference.
- [ ] Proactive commentary responds to verified race events.
- [ ] The engineer can initiate a crash check-in.
- [ ] Race conversations and incidents are remembered.
- [ ] Starting fuel can be estimated from historical consumption.
- [ ] Approved setup changes can be written to validated JSON files.
- [ ] The dashboard exposes telemetry, event logs, audio history and API costs.
- [ ] The system handles API failures without disabling local safety warnings.
- [ ] All target latency and game-performance impacts are measured.

---

# 23. Open Technical Questions

The following remain unresolved and require documentation checks or practical testing:

1. Exact DeepSeek model availability, vision support, pricing and latency through OpenRouter.
2. Fish Audio Drama 3's actual latency, expressive controls and streaming compatibility through OpenRouter.
3. Selection of a low-cost speech recognition provider.
4. Reliability of each ACC shared-memory field and broadcasting event during actual races.
5. Whether ACC's existing engineer can be sufficiently muted without losing other important game audio.
6. Actual Xbox R3 input behaviour and potential control conflicts.
7. Saved setup-field conversions and the extent to which active settings can be independently verified.
8. Accuracy of inferred incidents involving other competitors.
9. Actual weekly API costs under realistic race usage.
10. End-to-end latency under normal gaming conditions.

These are validation requirements, not assumed capabilities.

---

# 24. Final Product Definition

**ACC AI Race Engineer V1** is a Windows application that combines ACC telemetry, deterministic event processing, DeepSeek conversational intelligence and Fish Audio speech synthesis to provide a realistic, proactive and emotionally responsive motorsport race engineer.

It uses an Xbox controller radio toggle, replaces supported standard race-radio announcements, monitors the car and competitors, offers strategy and fuel guidance, reacts to accidents, maintains contextual race memory and provides a comprehensive local dashboard for configuration, monitoring, audio review and debugging.

The product prioritises authenticity, reliability, low latency and affordable operation.

**Final agreed radio control:** Xbox right-stick click (R3), press once to start recording and press again to stop and submit. No wake word.

**Final agreed V1 deployment:** Gaming desktop with cloud-hosted DeepSeek and Fish Audio through OpenRouter. No separate AI laptop or local language model required.