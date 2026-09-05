# First orders and borrowed hardware

Checked September 4, 2026. These are product candidates, not a purchased cart. Amazon delivery dates, selected variants, seller identity, stock and prices must be checked using the actual delivery address. No delivery dates are verified here.

## Order / borrow now

| Item | Quantity | Why / conditions |
| --- | --- | --- |
| [CanaKit Raspberry Pi 5 starter kit](https://www.amazon.com/dp/B0CRSNCJ6Y) | 1 if no spare Pi; not required for today's simulator | Linked variant is 8GB. Evaluate as an I/O controller/client; fully onboard STT + LLM + TTS latency is not verified. The current local voice runs on a Mac, not the Pi. Verify PSU, cooling and microSD are included in the selected kit. Start with its wall supply for bench work. |
| [Anker PowerConf S330 USB speakerphone](https://www.amazon.com/dp/B09FJ7LWX4) | 1 if no borrowed speakerphone | Microphones and speaker in one unit for laptop voice development. This is a bench audio choice, not a promise it will fit inside Bob. Laptop audio can be used immediately. |
| [Waveshare 1.28-inch GC9A01 round LCD](https://www.amazon.com/dp/B08V5538C6) | 1 evaluation unit; eventually 2 | SPI display, not a USB monitor. Active circle is 32.4 mm diameter; module is approximately 40.4 × 37.5 mm. Needs controller code and wiring. Confirm eye size and goggle clearance before committing both eyes to this size. |
| USB data cables, jumper wires, small breadboard, heat-shrink, M2/M3 assortment | As needed | Borrow first; check connector types. Dupont jumpers are for signals/light loads, not motor power. |
| [Bob plush at Target](https://www.target.com/p/-/A-89787224) | 1 | Verified candidate for the desired scale: 12.5 × 8.5 × 6 inches. This listing says Target exclusive; an equivalent Amazon donor body has not been verified. Weighted fill must be removed for the conversion. |

Product documentation:
- [CanaKit kit contents](https://www.canakit.com/canakit-raspberry-pi-5-starter-kit-turbine-black.html)
- [Anker S330 features](https://uk.ankerwork.com/products/a3308): USB audio, full-duplex and echo cancellation. Use it for both capture and playback. The manufacturer FAQ calls for USB power of at least 5V/2A; confirm supply/port capability, especially when adding it to a Pi.
- [Waveshare LCD dimensions and interface](https://www.waveshare.com/wiki/1.28inch_LCD_Module)

## Resolve before the next order

1. Borrowed arm: exact model, controller, matched supply, leader arm if available. Keep a borrowed SO-101 intact for bench experiments.
2. Base: actual usable plush footprint, total loaded mass, wheel clearance, control interface. A chassis shown in an online photograph is not a fit check.
3. Compact arm: one fixed banana cradle permits a simpler lift-and-grip mechanism. Choose servos from measured lever arms and total loads, not advertised stall torque alone.
4. Battery and regulator: match the selected base, servo voltage and Pi demand. The Pi wall supply in the first kit is for bench work; a generic USB power bank is not automatically suitable for the whole robot.
5. Final eyes: compare 32.4 mm visible circles against the actual goggles. A larger display may better fit Bob's face, but must also fit the PCB and connector envelope.

## Work immediately

Use the local face/behavior simulator. Start conversational audio on a laptop. If an arm is available, test a real banana handoff independently. These tasks do not need the plush or mobile base to arrive first.
