# JLCPCB fabrication and assembly files, rev A

| File | Upload to |
| --- | --- |
| `ionos_maximus_gerber.zip` | PCB order: Gerber + Excellon (2 layers, absolute origin, Protel extensions) |
| `ionos_maximus_bom_jlc.csv` | PCBA: BOM (one line per LCSC code) |
| `ionos_maximus_cpl_jlc.csv` | PCBA: CPL / pick and place |
| `ionos_maximus_bom_full.csv` | reference: same parts with maker, MPN, Basic/Extended |
| `not_assembled.csv` | parts JLC does not place |
| `lcsc_parts.csv` | part table (value + footprint -> LCSC code), every code checked on jlcpcb.com / lcsc.com |

## PCB order

- 2 layers, 1.6 mm FR-4, 1 oz outer copper, any colour, HASL (lead-free) or ENIG.
- Board size 45 x 39.1 mm.
- Via hole 0.3 mm. Minimum via diameter 0.45 mm (ring 0.075 mm, JLC accepts it; DFM shows a warning only).
- Before the Gerbers were made, the board was checked against JLC's DFM rules:
  - via ring to SMD pad >= 0.15 mm (own net included);
  - silkscreen >= 0.15 mm from pads and from every drilled hole.
- KiCad DRC: 0 errors, 0 unconnected, schematic parity 0.

## Assembly (PCBA)

- Assembly side: **Top**. All 26 placed parts are on the top side, and the bottom side has none.
- 12 BOM lines: 8 Basic and 4 Extended (LP5907, CAT24C32YI-GT3, TFM-120, 3.9k).
- **J1 (Samtec CLH-115-F-D-DV) is not in the BOM.** LCSC does not stock it. Either add it through JLC Global Sourcing in the BOM step, or order it from Samtec / DigiKey and solder it by hand. It is an SMT part on the top side.
- **P200 / P201 = Samtec TFM-120-02-L-D-K-TR (C17227108)**, the same substitute as on ionos-sdr-mini, because TFC-120 is not stocked.
  - JLC has no EasyEDA model of this part, so the CPL keeps KiCad's origin and rotation (body centre).
  - The 2x20 land pattern is symmetric and the part has no peg, so a 180 deg turn is still electrically correct.
  - Still look at the pin-1 mark in JLC's placement preview.
- CPL rotations and centres come from fitting JLC's own footprint of each LCSC part onto the board's pads. All 26 parts fit their land pattern.
- Not placed:
  - the solder jumpers (bridged in copper, JP8 open);
  - test pads TP1-TP7;
  - DNP parts R2, R13, R18, R19.
- In JLC's tool, re-check stock and confirm the part list. Stock and prices move.
