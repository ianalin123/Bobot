# Compatibility before purchases

The software boundaries are designed to be portable; **no complete bill of materials or physical fit has been verified**. Borrow what is available, record exact part numbers, and avoid locking mechanics to an online photograph.

| Subsystem | Evaluate now | Required before committing final design |
| --- | --- | --- |
| Compute | Existing Mac for fully local development; spare Pi for I/O | Benchmark all models concurrently on intended onboard compute; verify power, memory, cooling and weight |
| Audio | Laptop mic/speaker or USB speakerphone | Linux USB Audio compatibility, AEC performance with both directions on same device, power draw, body clearance |
| Eyes | One SPI GC9A01 evaluation screen | Logic levels, controller support, independent chip-select pins, frame bandwidth, active circle vs PCB/goggle dimensions |
| Arm | Borrowed SO-101 on its matched supply/controller | Exact servo/bus version, firmware, controller access, safe limits, reach and payload; do not assume all SO-101 variants match |
| Compact mechanism | Fixed cradle and short lift/grip | Measured banana sizes, torque across travel, grip force, jam detection, removable food-contact cradle |
| Base | Borrowed controllable differential drive | Documented interface, loaded mass, footprint, caster/wheel clearance, center of mass with arm extended, stop behavior |
| Body | Donor Bob plush | Remove loose weighting/stuffing as appropriate; rigid load-bearing frame, pinch guards, ventilation, access panels |
| Power | Matched wall supplies on supervised bench | Separately budget motor peaks and compute demand, matched regulated rails, fusing, rated wiring/connectors, physical stop and battery protection |

Do not power servos/motors from Pi GPIO or route motor current through breadboard/Dupont signal jumpers. Match grounding and logic levels to the actual controller interfaces. A generic USB power bank does not establish compatibility for Pi + audio + motors. Use an off-the-shelf protected battery system with its matched charger only after the actual load is known; this repository does not specify a battery build.

Record the available hardware here before implementing a physical adapter:

- Base/model/controller/interface: **unknown**
- Arm/servo models and voltage/controller: **unknown**
- Eye module/goggle measured space: **unknown**
- Audio model and USB power: **unknown**
- Total loaded mass and frame footprint: **unknown**
- Intended onboard compute and voice benchmarks: **unknown**

See [ordering candidates](../ORDERING.md) for evaluated listings. Purchases, delivery dates and fit are not verified by these documents.
