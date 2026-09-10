# Build plan

## September 10 event build (implemented, hardware-unverified)

The Jetson build described in [the design spec](superpowers/specs/2026-09-10-bob-event-robot-design.md) is implemented on branch `jetson-event`: cloud voice with local fallback, YuNet/SFace face recognition with consented enrollment, ESP32 LCD eye firmware plus a browser eye simulator, a Feetech bus driver for the SO-101 arm and LeKiwi base, a Director behavior layer, a phone console with e-stop and dead-man wander, and a one-shot Jetson setup script with an offline USB bundle. All of it is tested against fakes on a Mac; nothing below has touched a motor. Follow [DEPLOY.md](DEPLOY.md) on hardware day and treat every hardware step as a validation gate, not a promise.

## Week-one demo definition

A wheeled companion carries one manually loaded banana, retrieves/presents it from a fixed body cradle with a borrowed 12V SO-101 mounted on its LeKiwi base, talks using the existing offboard computer over Wi-Fi, and stops speaking when interrupted. A Pi handles robot-local I/O. Manual driving and recipient confirmation are acceptable. A one-foot plush is the character shell, not a validated overall robot size. No table pickup, autonomous person following, face identification, or LinkedIn integration is required for the competition demo.

September 6: 12V arm confirmed by user; first batch in [ORDERING.md](../ORDERING.md). Physical adapter, authenticated robot transport and Pi audio/eye clients are still unimplemented. The schedule is relative to build start and hardware availability, not a delivery promise.

| Time | Deliverable | Acceptance gate |
| --- | --- | --- |
| Day 1 | Local simulator and voice baseline | Automated tests + synthetic pipeline pass; teammate tests live mic and barge-in with real speaker setup |
| Day 2 | Borrowed hardware inventory and bench audio | Record exact controllers, voltages, interfaces, payload/envelope; 20 conversational interruptions with no stale reply |
| Day 3 | One banana cradle and bench arm sequence | At least 10 slow supervised handoffs; grip confirmation and physical stop work; no plush near pinch points |
| Day 4 | First hardware adapter | Same behavior tests pass with a hardware test double; real stop/watchdog/disconnect tests pass before unsupervised power |
| Day 5 | Mobile base, manual control, rigid internal frame | Measure loaded stability and floor clearance; low-speed operation; isolated motor/compute power paths; no exposed wiring |
| Day 6 | Eyes, donor plush, cable routing | Electronics ventilated, moving parts guarded, battery accessible; actual goggle PCB clearance checked |
| Day 7 | Rehearsal and reliability pass | 20 supervised banana cycles without jam, drop, brownout or tip; stop and recovery drills; offline voice demo with downloads cached |

If mechanics slip, demonstrate the working local voice/face on a manually driven base with a fixed banana tray; present the arm as unfinished. Do not hide failures with scripted claims. Freeze new features before the final rehearsal.

## Mobile integration sequence

Bench arm on existing controller/supply → unloaded base with independent stop → measured arm/base/cradle layout → authenticated network transport and local disconnect behavior → robot audio/eye clients → loaded tests → plush. Travel only with arm stowed; park before arm movement. Evaluate support during torque-off rather than assuming cutting power holds the arm.

## Simulation improvements

1. Expand the existing adapter into deterministic fault scenarios: late/missing acknowledgements, grip failure, jam, empty cradle, controller disconnect, stale sensor data, interrupted delivery. Add replayable event traces with explicit user opt-in before storing conversation data.
2. Add a real kinematic model only after measuring the selected arm/cradle. Store calibrated joint limits and named poses separately from behavior. Ensure visual poses are driven by measured or simulated joint state, not a separate UI timeline.
3. Add wheel odometry and navigation in a separate subsystem. Reuse destination-level commands; keep velocity safety and watchdogs below the LLM. Begin with manual driving, then fiducial/station navigation before general person delivery.
4. Build CAD/URDF and collision/center-of-mass models from the chosen hardware, then use a robotics simulator for reach, clearance and navigation tests. A cute CSS preview does not validate physics or sim-to-real transfer.

## Onboard local inference gate

The current M4/16GB development Mac proves software integration, not Raspberry Pi performance. Before buying final compute, benchmark the exact local STT, model and TTS simultaneously on candidate hardware, on battery, inside a thermally representative enclosure.

Measure cold/warm start, end-of-speech to audible reply p50/p95, interruption-to-silence latency, transcription accuracy in room noise, sustained memory/temperature/power, and 30+ minutes of conversation without backlog. Initial targets to test—not current measured guarantees—are warm audible reply under 2 seconds median and 4 seconds p95, local playback stop under 250 ms from speech onset, and no stale audio after cancellation.

If a Pi misses the target, keep it for I/O and choose more capable onboard compute, or shrink models/response length. A development laptop connected over Wi-Fi is offboard inference even when no cloud is used. The long-term all-onboard build needs a mass, cooling and battery budget for its actual compute.

## Introductions and LinkedIn, later

Interpret “recognizes a person” as **detecting someone nearby**, then making an introduction. Start with local person-presence detection, no identity inference from an unknown face. Bob asks the person for their name/company and whether they want a profile lookup. They can provide a profile link or QR code; otherwise show candidates for confirmation. Greet by confirmed name and use only explicitly shared context. An optional remembered acquaintance feature would need separate enrollment/consent and deletion controls.

Public LinkedIn lookup/browser navigation needs internet even if perception and reasoning stay local. Keep it an explicit, optional module; do not claim fully offline lookup. Let the person confirm the profile, and never silently message people, access private profiles, harvest contacts, or treat a guessed match as identity. An offline demo can use an attendee-provided profile card/QR instead. Current Bob cannot see, identify, browse, or look up profiles.

## Team handoff

Create hardware/adapter, audio/latency, and enclosure/face workstreams only after agreeing owners. Invite collaborators by their exact GitHub username with write access, not guessed identity. Keep repository private until the team chooses code licensing and handles character/asset rights.
