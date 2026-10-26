# ionos-maximus-rpi-sdr

<p align="center"><img src="docs/img/banner.png" width="100%" alt="IonosSDR maximus"></p>

Raspberry Pi host variant of the [Ionos SDR](https://github.com/ionos-sdr/ionos-sdr) platform: an EFR32FG23 sub-GHz SoC radio streaming I/Q directly to a Raspberry Pi over SPI. The goal is to lift the current ~250 ksps host-link ceiling to ~1 Msps.

Status: **concept / measurement phase**. Nothing in this repository is validated unless it is explicitly marked as measured.

## Motivation

On the ESP32-S3 based Ionos SDR / ionos-mini cradle, the radio is not the bottleneck:

| Link | Throughput | Status |
|---|---|---|
| FG23 RAIL I/Q capture | 1000 ksps @ 270 kHz filter BW (fs = 39 MHz / 39) | measured; zero-copy FIFO read, ~11 % CPU |
| FG23 → ESP32-S3 (SPI2 IO_MUX) | not limiting | |
| ESP32-S3 → host, USB HWCDC | ~250 ksps int16 | measured; USB Full-Speed, ~1 MB/s practical |
| ESP32-S3 → host, WiFi (SpyServer) | ~175 ksps int16 | measured; ~712 kB/s with PSRAM lwIP buffers |

The ceiling is the ESP32-S3 Full-Speed USB / WiFi uplink. A Raspberry Pi host removes that hop: samples land directly in Linux, where SDR++, SoapySDR or GNU Radio run locally or forward over Gigabit Ethernet.

## Architecture

```
 Silabs radio board (BRD4265B, FG23)          Raspberry Pi 4 / 5
 ┌───────────────────────────────┐           ┌──────────────────────────────┐
 │ RAIL RX FIFO (RAM)            │  SCLK  ◄──┤ SPI0 main (DMA)              │
 │   └─ LDMA ─► EUSART secondary ├─ MISO  ──►│   └─ libionos ring buffer    │
 │                               │  CS    ◄──┤       ├─ SoapySDR module     │
 │                               │  DRDY  ──►│ IRQ   └─ SDR++ source        │
 │ cmdlink UART RX               │  CMD   ◄──┤ UART TX                      │
 │ SWD / RESETn                  │  SWD   ◄──┤ GPIO (OpenOCD / pyOCD)       │
 └───────────────────────────────┘           └──────────────────────────────┘
```

Design decisions:

- **SPI, not a parallel bus.** The FG23 has no external parallel bus; the Raspberry Pi DPI/SMI interfaces would need an LDMA→GPIO bit-bang on the FG23 side and more mezzanine pins than exist. One SPI lane carries 1 Msps if the samples are packed (see *Rate budget*).
- **Two link modes on the same nets, selected in software** (see *Link modes*). Mode A: the Raspberry Pi is the SPI main and the FG23 the secondary. Mode B (Pi 5 only): the FG23 is the main and the RP1 PIO acts as the secondary. No solder jumpers select the mode.
- **Zero-CPU data path on the FG23.** LDMA moves samples from the RAIL RX FIFO straight to the EUSART TX register.
- **Same mezzanine pins as ionos-mini.** The HAT reuses the Silabs radio-board socket and the FG23 pins already validated on ionos-mini; only the roles change.
- **Sample format:** int16 LE, Q-first (`struct { int16_t q, i; }`), as in Ionos SDR, plus packed-12 and int8 modes for high rates.

## Pin map (FG23 radio board ↔ Raspberry Pi 40-pin header)

FG23 ports are on BRD4265B (EFR32FG23B010F512IM48, QFN48). The mezzanine pins are the radio-board socket (P200 = CON1, P201 = CON2) as used on ionos-mini. The Raspberry Pi side uses BCM GPIO numbering, with the physical header pin in brackets.

### Data and control

| Signal | Dir | FG23 port | Mezzanine | RPi GPIO [pin] | RPi function | Notes |
|---|---|---|---|---|---|---|
| IQ_SCLK | Pi → FG23 | PC05 | P201-15 | GPIO11 [23] | SPI0 SCLK | Direction per link mode. 22 Ω series at both ends |
| IQ_DATA | FG23 → Pi | PC00 | P201-10 | GPIO9 [21] | SPI0 MISO | I/Q stream. 22 Ω series at both ends |
| IQ_CS | Pi → FG23 | PA07 | P201-13 | GPIO8 [24] | SPI0 CE0 | Direction per link mode. 22 Ω series at both ends; 10 kΩ pull-up to 3V3 keeps the link idle while either side boots |
| IQ_MOSI | Pi → FG23 | PA05 | P201-7 | GPIO10 [19] | SPI0 MOSI | Optional in-band commands (full duplex). Was FG23_SPARE; 0 Ω DNP footprint |
| DRDY | FG23 → Pi | PD02 | P201-9 | GPIO25 [22] | GPIO IRQ | Mode A: FG23 → Pi, block ready. Mode B: Pi → FG23, host DMA armed (as RDY on ionos-mini). 22 Ω series at both ends, 10 kΩ pull-down |
| CMD | Pi → FG23 | PA06 | P201-11 | GPIO14 [8] | UART TXD | Existing `cmdlink` RX. Needs the PL011 on GPIO14 (`dtoverlay=disable-bt` or `miniuart-bt`) |

Debug solder jumpers: SJ1 (IQ_SCLK) and SJ2 (IQ_CS), 2-pad, normally closed, cuttable, at the Pi header. They are not mode selectors: cutting them isolates the Pi so that the FG23 side can be probed alone.

### Link modes

| | Mode A (default) | Mode B |
|---|---|---|
| SPI main | Raspberry Pi SPI0 | FG23 EUSART/USART, 19.5 MHz (39 MHz / 2) |
| SPI secondary | FG23 EUSART | Pi 5 RP1 PIO state machine on GPIO8–11 |
| Pi side setup | `dtoverlay=spi0-1cs` | SPI0 disabled; GPIO8–11 assigned to PIO (PIOLib) |
| DRDY / RDY on PD02 ↔ GPIO25 | FG23 → Pi | Pi → FG23 |
| Supported hosts | Pi 4, Pi 5 | Pi 5 only (the Pi 4 has no PIO; its BSC-based SPI secondary is too slow) |

Both modes use identical wiring, and the mode is set by the FG23 firmware and the Pi configuration. The 22 Ω series resistors at both ends keep the termination correct in either drive direction and limit the current if both sides drive a net during a misconfiguration.

Mode B risk: PIOLib reaches the RP1 PIO from Linux through a firmware mailbox, with at least ~10 µs per operation; the state-machine FIFOs are DMA-capable, but no throughput figure is published. M1 measures it.

### Debug and programming

| Signal | Dir | FG23 port | Mezzanine | RPi GPIO [pin] | Notes |
|---|---|---|---|---|---|
| SWDIO | bidir | PA02 | P201-21 | GPIO24 [18] | OpenOCD / pyOCD via `linuxgpiod` — flash the FG23 from the Pi |
| SWCLK | Pi → FG23 | PA01 | P201-23 | GPIO23 [16] | |
| SWO | FG23 → Pi | PA03 | P201-19 | GPIO27 [13] | Optional; SWV trace or UART log |
| RESETn | Pi → FG23 | RESETn | P200-17 | GPIO22 [15] | Drive open-drain only; no external pull-up on the radio board |

### Board ID, power, ground

| Signal | Mezzanine | RPi [pin] | Notes |
|---|---|---|---|
| BOARD_ID_SDA | P200-40 | GPIO2 / SDA1 [3] | Radio-board M24C02; already has 10 kΩ pull-ups on the radio board |
| BOARD_ID_SCL | P200-39 | GPIO3 / SCL1 [5] | |
| HAT ID EEPROM | — | GPIO0 / GPIO1 [27 / 28] | HAT's own EEPROM (HAT+ spec), auto-loads the device-tree overlay |
| VMCU_IN | P201-2 | 5 V [2, 4] → LDO | Dedicated LP5907-3.3 (as on ionos-mini), not the Pi 3V3 rail |
| 3V3_RADIO | P200-1 | from the same LDO | Board-ID EEPROM supply |
| GND | P200-2, P200-38, P201-1, P201-39 | [6, 9, 14, 20, 25, 30, 34, 39] | Continuous plane under the socket and the header |
| USB_VREG | P200-36 | — | Radio-board output, never drive |

Kept free on the Pi side: GPIO18–21 (PCM / I2S), GPIO12/13 (PWM), SPI1. These stay available for a future audio codec or a second HAT.

## Rate budget

Payload rate = fs × 2 × bits per component.

| fs | int16 | packed 12-bit | int8 |
|---|---|---|---|
| 250 ksps | 8 Mbit/s | 6 Mbit/s | 4 Mbit/s |
| 500 ksps | 16 Mbit/s | 12 Mbit/s | 8 Mbit/s |
| 780 ksps (39 MHz / 50) | 25 Mbit/s | 18.7 Mbit/s | 12.5 Mbit/s |
| 1000 ksps (39 MHz / 39) | 32 Mbit/s | 24 Mbit/s | 16 Mbit/s |

Design rule: SCLK ≥ 1.5 × payload rate, to absorb inter-burst gaps in the Linux SPI driver. The Pi SPI0 clock is an even divider of the core or reference clock, so only discrete rates are available (e.g. about 20.8 / 25 / 31.25 MHz on Pi 4).

Consequences:

- 1 Msps needs **packed-12 or int8**; int16 at 1 Msps does not fit one lane.
- Int16 is realistic up to ~500–650 ksps.
- The binding limit is most likely the **FG23 EUSART secondary-mode maximum SCLK**, not the Pi (measurement M1).

## FG23 firmware changes

1. EUSART (or USART) on PC05 / PC00 / PA07: **secondary** for mode A, **main** for mode B (as on ionos-mini), selected at build or run time; confirm the routing in the Pin Tool.
2. LDMA: RAIL RX FIFO → EUSART TXDATA, block-sized descriptors, DRDY (PD02) raised per block.
3. PD02 as an output (it is an input on ionos-mini).
4. Frame header per block: sync word, sequence counter, sample format, overflow flag.
5. Optional: PA05 as secondary RX for in-band commands.

## Measurement plan

Details in [docs/measurement-plan.md](docs/measurement-plan.md).

| ID | Question | Pass criterion |
|---|---|---|
| M1 | Max reliable FG23 SPI secondary SCLK | ≥ 25 MHz with zero sequence/CRC errors over 10⁹ bits |
| M2 | Effective number of bits in FG23 I/Q | decides int16 vs packed-12 vs int8 |
| M3 | RX bandwidth beyond 270 kHz (`k` benchmark, N < 39) | usable flat BW and alias rejection per N |
| M4 | End-to-end sustained rate into the Pi | no dropped blocks over 1 h at the target rate |
| M5 | Pi-side CPU load and latency (Pi 4 vs Pi 5) | headroom for local SDR++ |

## Related repositories

- [ionos-sdr/ionos-sdr](https://github.com/ionos-sdr/ionos-sdr) – firmware, `libionos`, host software
- [ionos-sdr/ionos-sdr-mini](https://github.com/ionos-sdr/ionos-sdr-mini) – ESP32-S3 radio-board cradle

## Licensing

- Firmware and host code: MIT (see [LICENSE](LICENSE))
- Hardware (when added): CERN-OHL-P-2.0
- Documentation and measurement data: CC-BY-4.0

## Credits

Zoltán Dóczi (HA7DCD) and Zoltán Papp – [Z2 Labs](https://z2labs.io)
