# Measurement plan

All results go to `measurements/<ID>-<date>/` with raw data, the script used, and a short README. Report a result as validated only when the raw data is committed.

## Setup

- Raspberry Pi 4 (4 GB) and Raspberry Pi 5, Raspberry Pi OS 64-bit, `spidev` with DMA.
- BRD4265B radio board on a prototype adapter (fly-wire first, then the HAT).
- Signal source: VSG60 or HackRF; spectrum reference: SA/VSA as available.

## M1 — FG23 SPI secondary maximum clock

1. FG23 firmware sends a deterministic test pattern (32-bit counter + CRC-16 per block) over EUSART secondary.
2. The Pi reads fixed-size blocks on DRDY at SCLK = 10, 15.6, 20.8, 25, 31.25 MHz (Pi 4 dividers; repeat with Pi 5 rates).
3. Record the bit error rate, sequence gaps and DRDY-to-CS latency.
4. Mode B (Pi 5 only): FG23 as main at 19.5 MHz (39 MHz / 2), RP1 PIO state machine as secondary on GPIO8–11, DMA from the PIO RX FIFO via PIOLib. Record the sustained throughput, dropped blocks and Pi CPU load.
5. Compare modes A and B on the same hardware. The only change is the Pi configuration and the FG23 firmware build.

Pass (mode A): ≥ 25 MHz with zero errors over 10⁹ bits.
Pass (mode B): ≥ 19.5 Mbit/s sustained with zero dropped blocks over 10⁹ bits.

## M2 — Effective number of bits of the FG23 I/Q

1. Terminated input: histogram and RMS of I and Q at each fs.
2. CW tone at −60 / −40 / −20 dBm: SINAD → ENOB.
3. Check whether the low bits carry information (LSB toggle statistics).

Outcome: chosen wire format (int16, packed-12 or int8).

## M3 — RX bandwidth beyond 270 kHz

1. Run the `k` benchmark for N = 39, 30, 26, 20 (fs = 1.0 / 1.3 / 1.5 / 1.95 Msps).
2. Sweep a CW tone across ±fs/2; record the passband flatness and the alias rejection.
3. Confirm the decimator / channel-filter configuration that the Radio Configurator allows.

Outcome: maximum usable I/Q bandwidth per fs.

## M4 — End-to-end sustained rate

1. Stream at the target fs and format for 1 h into a `libionos` ring buffer.
2. Log dropped blocks, overflow flags, and Pi kernel latency (`cyclictest` in parallel).

Pass: zero dropped blocks.

## M5 — Pi-side load

1. CPU load of the reader thread, plus SDR++ running locally with waterfall at the target rate.
2. Compare Pi 4 and Pi 5.
