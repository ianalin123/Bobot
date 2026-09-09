# Hardware baseline: 12V mobile-arm Bob

Updated September 6, 2026. **Design baseline, not physical validation.** User confirmed a borrowed 12V SO-101 on the moving robot. Reference video shows an SO-family printed arm and controller, not readable servo/controller labels or a calibrated size reference.

## Decisions and validation gates

| Subsystem | Baseline | Gate |
| --- | --- | --- |
| AI | Existing offboard computer over Wi-Fi | Validate network/audio latency; keep providers local to computer. No Pi inference purchase. |
| I/O | Pi 5 4GB reference host, borrow if possible | OS, USB inventory, power, cooling and network reachability. |
| Arm | Borrowed 12V SO-101 follower + existing controller/supply | Labels, protocol, firmware, limits, polarity, current ratings and mounting pattern before rewiring. |
| Base | Seeed LeKiwi 12V kit, SKU 114090065 | Assemble; verify drive, stop, added payload layout and stability. |
| Audio | ReSpeaker Lite USB + Seeed 114993346 enclosed speaker | Board 35 × 86 mm; speaker 50 × 45 × 22 mm. Check lead and test simultaneous capture/playback, motor noise and interruptions. |
| Eyes | Waveshare SKU 28514: evaluate one, then two | PCB 55 × 55 mm each; check depth/goggles/cables, firmware and power. |
| Body | Target 89787224 shell | External 317.5 H × 215.9 W × 152.4 D mm is not interior space. Remove weighting; weigh shell and measure goggles. |
| Banana | One manually loaded removable cradle | Actual dimensions, mass, grip, retention, release; park base for manipulation. |
| Power | Separate matched bench supplies; battery later | Current/voltage tests, rated distribution, fuses, independent motor stop and protected battery/matched charger. |

## Layout and size

Arm mounts directly to a rigid base-supported bracket. Keep battery mass low and mechanically retained. Keep fabric out of joints, gripper, wheels and cooling paths. Initially expose the arm. Hiding an intact SO-101 inside a one-foot plush is not a validated fit.

Upstream `3DPrintMeshes/base_plate_layer2.stl` bounds measured during research were approximately **215.87 × 209 × 7 mm**, interpreted in the project's millimeter print scale. This is the upstream top plate only, not complete wheel footprint, support polygon, certification of the Seeed kit or total height. Actual assembled dimensions and loaded mass remain unmeasured.

Record wheel contact points, loaded center of mass and worst-case arm/banana extension. Test stability while supervised and with conservative limits. Travel with arm stowed; park before arm movement. These are mitigations, not proof of safety. Torque-off may let the arm fall: evaluate mechanical support and clear space during power-stop tests.

## Electrical and control boundaries

- 12V is user-confirmed. It does not establish peak current, exact servo protocol or connector pinout.
- Keep borrowed arm controller intact initially. Separate arm/base USB controllers need an explicit adapter; stock LeKiwi normally shares a motor bus. Verify protocol, IDs and current-rated wiring before combining. Preserve borrowed configuration and obtain owner agreement before re-ID/reflash changes.
- Reserve Pi USB ports for two controllers, ReSpeaker and optionally one camera. Eye Wi-Fi updates avoid data-port use but still need separately budgeted 5V power. More devices need a hub/power plan.
- Do not power motors from GPIO, USB VBUS, breadboards or signal jumpers. Never parallel supplies or assume a nominal 12V battery is regulated.
- Verify selected kit battery inclusion and converter capacity. Listed 12V/2A supply is not approved for the combined robot.
- Require independent motor stop and robot-local watchdogs; deliberate re-arm after a fault, no motion automatically on reconnect/reboot.

## Intake checklist

- [x] Arm mounted on moving base: user confirmed.
- [x] 12V arm variant: user confirmed, not independently measured.
- [x] Offboard compute over Wi-Fi allowed.
- [ ] Actual follower/controller/servo labels, firmware, supply rating and polarity.
- [ ] Base received; selected contents and controller documented.
- [ ] Original calibration/configuration saved before approved modifications.
- [ ] Loaded mass, envelope, wheel contacts, mounting and arm clearance.
- [ ] Rated power/protection/distribution, brownout and stop tests.
- [ ] Grip/cradle confirmation and supervised handoffs.
- [ ] Authenticated network transport, disconnect behavior and reconnect tests.
- [ ] Audio duplex, motor-noise, reconnect and enclosure interruption tests.
- [ ] Eye boards fit actual goggles or dimensioned replacement mounts.

The runtime still uses SimHardware. These documents do not implement physical control or enable network deployment. Sources and purchase statuses: [ORDERING.md](../ORDERING.md).
