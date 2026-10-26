# ionos-maximus RPi adapter, rev A (KiCad 10)

A carrier between a Raspberry Pi and a Silicon Labs radio board (BRD4265B, EFR32FG23). No ESP32: the Pi reads the I/Q stream directly over SPI0 (link modes A/B, see the top-level README).

**Status: schematic done (ERC 0, house-style schematic), parts placed (DRC 0 errors, schematic parity 0), not routed yet.** Schematic: [ionos_maximus_schematic.pdf](ionos_maximus_schematic.pdf).

## Mechanics

The board frame, outline and Pi connector are taken 1:1 from [RPi_ISM](https://github.com/rfsparkling/RPi_ISM) rev1, so the board sits on the Pi exactly like RPi_ISM:

- 45 x 39.1 mm, same outline, notch and slot.
- J1 = Samtec CLH-115-F-D-DV on RPi header pins 1-30, RPi_ISM socket numbering: J1 pin n = RPi pin n+1 (n odd) / n-1 (n even).
- Every pin the README pin map needs lies within pins 1-30, including ID_SD / ID_SC (27 / 28).

The radio board sits on P200 / P201 (2x20, 1.27 mm, 24 mm apart, footprint and JLC part as on ionos-sdr-mini). The pair is rotated 180 deg against ionos-sdr-mini, so the P201 outer row (all I/Q and SWD pins) faces the free top strip.

**To verify on a real BRD4265B before routing:** the radio-board outline on Dwgs.User assumes 32.5 mm along the connectors and 11 mm / 5 mm overhang beyond P200 / P201, and parts up to 1.1 mm high (0402, SOT-23-5, Tag-Connect pads) sit under the radio board.

## Circuit

| Block | Parts | Notes |
| --- | --- | --- |
| Radio supply | FB1, U1 LP5907-3.3, C1-C5, R1 0R, JP1 | Pi 5 V -> ferrite -> LDO -> VMCU_IN (P201-2); JP1 feeds the board-ID EEPROM (P200-1). R2 (DNP) offers 5 V on P200-37 |
| I/Q link | R3-R6, R8-R11 22R, R7 10k CS pull-up, R12 10k DRDY pull-down | 22R at both ends of SCLK, CS, DATA, DRDY. JP2 / JP3 = README SJ1 / SJ2 (cut to isolate the Pi). R13 (DNP) = IQ_MOSI |
| cmdlink | R14 0R | Pi PL011 TXD (GPIO14) -> PA06 |
| SWD | JP4-JP7, J2 (DNP, TC2030-NL) | OpenOCD linuxgpiod from the Pi; cut JP4-JP7 for an external probe |
| ID EEPROM | U2 CAT24C32WI-GT3, R15/R16 3.9k, R17 1k WP pull-up, JP8, TP3 | per the RPi HAT design guide, auto-loads the overlays; bridge JP8 to re-flash |

## Files

```
gen/design.py         netlist source of truth (parts, values, footprints, pin -> net)
gen/gen_sch.py        writes kicad/ionos_maximus.kicad_sch + .kicad_sym
gen/mkfp.py           builds kicad/ionos_maximus.pretty from ionos-sdr-mini and RPi_ISM
gen/gen_pcb.py        placement, outline (from RPi_ISM), GND pours (KiCad python)
gen/check_pinmap.py   independent check of the netlist against the README pin map
kicad/                KiCad 10 project, JLC 2-layer 1 oz rules (.kicad_dru from RPi_ISM)
kicad/3dmodels/       STEP models of every fitted part, referenced via ${KIPRJMOD}
```

Rebuild (Windows, KiCad 10): `build.ps1 -pcb`. `gen_pcb.py` overwrites the board, so run it only before routing.

## Open

1. Routing, GND stitching and the board checks.
2. LCSC fields, JLC BOM / CPL.
3. Fiducials, silkscreen title, ID EEPROM image (overlays: spi0-1cs, disable-bt).
