# Bob: 12V mobile-arm prototype shopping list

Updated September 9, 2026. User reports that the listed first-batch hardware has been ordered or borrowed. The **camera module is still missing**. Exact received contents, delivery status, delivery dates, address-specific availability and Amazon sellers remain unverified here.

This supersedes the small-rover / bench-only-arm plan. LeKiwi is the selected development platform, not a physically validated finished Bob. One-foot plush size does not mean one-foot overall robot height or that the arm fits inside it.

## First batch: buy or borrow for development

| Item | Qty | Selection / status |
| --- | --- | --- |
| Borrowed 12V SO-101 follower, controller, matched supply and USB data cable | 1 set | Borrow together; no duplicate arm purchase. Voltage confirmed by user; record exact servo/controller labels at intake. |
| [Seeed LeKiwi 12V base kit](https://www.seeedstudio.com/Lekiwi-Kit-p-6501.html), SKU 114090065 | 1 | $179 / listed in stock. Self-assembly base with printed parts and electronics; do not add another arm. Title mentions battery but wiki lists it as optional: check the selected package contents before paying. |
| [Raspberry Pi 5 4GB](https://www.raspberrypi.com/products/raspberry-pi-5/) | 1 if not borrowed | Robot I/O, not AI inference. Add official Active Cooler, official 27W USB-C bench supply and 32GB+ microSD if missing. No need for the earlier 8GB/128GB kit solely for I/O. |
| Camera module | 1 | **Missing from the current order.** Select a camera only after confirming Pi interface, cable/USB port budget, mounting position, field of view and privacy/shutdown behavior. Do not treat the optional spare USB port as an ordered camera. |
| [ReSpeaker Lite](https://www.seeedstudio.com/ReSpeaker-Lite-p-5928.html), SKU 107990273 | 1 evaluation unit | Select 2-mic / XU316 / without XIAO / without case. $24.90 / listed in stock. Board 35 × 86 mm; USB 5V, USB firmware. Test full-duplex and interruption behavior in Bob. |
| [Seeed Mono Enclosed Speaker 4Ω 5W](https://www.seeedstudio.com/Mono-Enclosed-Speaker-4R-5W-p-5931.html), SKU 114993346 | 1 | $2 / listed in stock. Manufacturer pairs it with ReSpeaker Lite. **50 × 45 × 22 mm**, already enclosed; check supplied lead/connector. Replaces bare Adafruit speaker candidate; no separate amplifier purchase for this pairing. |
| [Target Bob donor plush](https://www.target.com/p/-/A-89787224), TCIN 89787224 | 1 | $21.99 listed. External **12.5 H × 8.5 W × 6 D inches**, not usable interior dimensions. Remove weighting and weigh remaining shell. Not structural support. |
| USB data cables, hook-and-loop straps, cable ties, heat-shrink, M2/M3/M4 hardware | As needed | Borrow first; deduct kit contents. Match connector ends and fastener lengths to actual hardware. Signal jumpers/breadboards must not carry motor current. |

Bench USB plan: Pi ports for arm controller, base controller and ReSpeaker; one spare for an optional camera. This preserves borrowed arm electronics initially but needs a two-controller adapter: stock LeKiwi software expects a shared servo bus. Combine buses only after confirming protocol, IDs, wiring and current ratings. More USB devices require a new port/power budget.

## Eyes: evaluate one, then buy the matched pair

Candidate: [Waveshare ESP32-S3-Touch-LCD-1.85](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85), **SKU 28514, not B/C variants**. Buy one if ready to evaluate firmware and a custom goggle mount; final quantity two.

Each is a 360 × 360 round LCD plus ESP32-S3, with **55 × 55 mm PCB**. Two boards require at least **110 mm plus gap, mounting and cable clearance**. Nominal visible diameter is about 47 mm, not PCB width. Depth and actual goggle aperture remain unverified. These are development boards, not USB monitors; this repo does not yet contain their eye firmware.

If preserving original goggles is mandatory, measure them before buying final screens. Do not order two 1.28-inch panels on the assumption they fit or look right.

## Required before untethered operation: not yet a finalized order

| Assembly | Qty | Acceptance condition |
| --- | --- | --- |
| Protected motor battery with matching charger | 1 system | Actual servo voltage tolerance, continuous/peak demand, connector ratings, undervoltage behavior, mass and retention. A nominal 12V label or Ah rating alone is insufficient. |
| Regulated 5V compute/audio/eye power | 1 system | Pi-compatible supply and USB power budget under simultaneous load; verify kit converter capacity and USB-C behavior. |
| Motor disconnect, fuses, distribution and rated wiring/connectors | 1 system | Independently stop arm/base without Wi-Fi/Python; no auto-restart. Account for gravity-driven arm collapse when torque is removed. |
| Rigid arm mount, plush carrier, guards and removable banana cradle | 1 set | Measured hole pattern, arm sweep, wheel clearance, center of mass, retention and supervised tip tests. Fabric is not a load path. |
| Grip/cradle sensing | As tests require | Joint position alone does not prove a banana is held. Supervised manual confirmation is acceptable initially. |

Start on separate matched wall supplies. Seeed lists a **12V/2A** adapter; this is not approved here for combined arm + base + Pi loads. The upstream 12V/5A battery is a reference, not proof it covers our added hardware. Never parallel supply outputs or feed 12V into Pi/audio/eye USB ports.

## Evidence and reviews

- [Official LeKiwi setup](https://huggingface.co/docs/lerobot/lekiwi) documents mobile-arm/network control. [Owner issue #24](https://github.com/SIGRobotics-UIUC/LeKiwi/issues/24) reports combined arm/wheel failures; [issue #14](https://github.com/SIGRobotics-UIUC/LeKiwi/issues/14) reports URDF problems. Not reproduced here. Store shows one rating; its review text was not verified. This is not a thoroughly review-vetted appliance.
- [ReSpeaker owner discussion](https://forum.seeedstudio.com/t/3-broken-and-unusable-respeaker-lite/291165) includes failures and positive AEC/interruption reports; some failures involved I2S/soldering, not our USB path. Buy one for testing, not on a reliability guarantee.
- Visible Target reviews support plush feel/size, not robot conversion or eye clearance.
- Exact eye-board documentation exists; no independently reproduced reliability/fit test here.

Primary references: [Seeed base BOM/wiring](https://wiki.seeedstudio.com/lerobot_lekiwi/), [upstream BOM](https://github.com/SIGRobotics-UIUC/LeKiwi/blob/main/BOM.md), [ReSpeaker dimensions/USB firmware](https://wiki.seeedstudio.com/reSpeaker_usb_v3/), [speaker manufacturer datasheet](https://files.seeedstudio.com/Bazaar/product_pdf/114993346.pdf), [eye dimensions](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85).

## Immediate build sequence

1. Continue local simulator/voice on existing computer; no new AI computer or cloud account.
2. Inventory and rigidly secure borrowed arm; bench-test on its existing controller and matched supply.
3. Assemble unloaded base; test stop/reconnect/power behavior before adding load.
4. Use one manually loaded banana cradle and supervised manual driving. Stow before travel; park before arm movement.
5. Add body/eyes after rigid layout passes clearance/stability tests. See [hardware gates](docs/HARDWARE.md) and [roadmap](docs/ROADMAP.md).
