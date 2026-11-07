# FG23 multi-PHY packet sniffer firmware

RAIL application for the EFR32FG23 (BRD4265B radio board on ionos-maximus
rev A, or radio 2 of rev B). It receives **wM-Bus T/C/S**, **IEEE 802.15.4g
SUN-FSK** and **generic 2-FSK** packets natively in the radio's packet
modem (no I/Q involved) and forwards every frame with RSSI, LQI, RAIL
timestamp, PHY/sub-PHY id and CRC status to the Raspberry Pi.

Status: **written against the public RAIL 2.x API, not yet compiled or run
on hardware** (no Simplicity SDK in the authoring environment). Everything
listed under *To verify* must be checked on the owner's machine.

License: MIT (see `../../LICENSE`). RAIL is linked as a Zlib-licensed
library; no Silicon Labs source is included. The Silicon Labs component
config headers in `config/` are Zlib-licensed generated files.

## Files

| File | Role |
|---|---|
| `sniffer.c/.h` | RAIL core: PHY table, `RAIL_ConfigChannels` switching, wM-Bus T+C (`RAIL_WMBUS_Config`), 802.15.4g promiscuous init, packet drain, filters, hopping, text / rtl_wmbus output |
| `pktlink.h` | **Shared** PKT1 block format (1040 B, IQB2-compatible framing). Same file is the reference for `host/ionos_tools/pktlink.py` |
| `pktlink.c`, `pktlink_port.h` | Transport: SPI secondary (USART0 + LDMA + DRDY on PD02) or VCOM UART |
| `wmbus_frame.c/.h` | EN 13757-4 CRC-16, frame A/B CRC check and strip (own implementation, tested on the host too) |
| `cmd.c/.h` | One-line command parser, shared by VCOM and cmdlink |
| `cmdlink.c/.h` | Pi → FG23 command UART on PA06 (from the I/Q firmware) |
| `app.c`, `main.c` | Glue |
| `ionos_sniffer_fg23.slcp`, `config/` | Simplicity project and component configuration |
| `config/rail/radio_settings.radioconf` | Multi-PHY radio configuration (hand-authored, see below) |

## Build (owner's machine)

Tested target: Simplicity Studio 5 / 2025.x with Simplicity SDK 2025.6.x
(RAIL 2.19.x), GCC 12, part `EFR32FG23B010F512IM48`, boards `brd4265b` +
`brd4001a`.

1. Copy `firmware/sniffer/` into the Studio workspace, or **File → Import →
   More Import Options → Project (.slcp)** and point at
   `ionos_sniffer_fg23.slcp`. Studio asks for the SDK; choose 2025.6.x.
2. Open `config/rail/radio_settings.radioconf` in the Radio Configurator.
   If Studio accepts the file, check that the ten protocols are present in
   the order of the table below and save (this regenerates
   `autogen/rail_config.c/.h`). If it refuses the file, delete it and
   recreate the ten protocols with the recipe below, **in this order**.
3. Build (`Project → Build`). Flash with the J-Link of the WSTK, or from the
   Pi over SWD (`openocd -f interface/raspberrypi-native.cfg ...`, see the
   repository README).
4. Open the VCOM port at **921600 8N1** (set in
   `config/sl_iostream_eusart_vcom_config.h`). You should see
   `# ionos-maximus sniffer 0.1.0 (? = help)`.

Alternative CLI build: `slc generate -p ionos_sniffer_fg23.slcp -d build
--with brd4265b,brd4001a && make -C build -f ionos_sniffer_fg23.Makefile`.

### Radio configuration recipe

Protocol index = `pktlink_phy_t` = `channelConfigs[]` index. `sniffer.c`
`PHY_TABLE` hard-codes these indices; if you reorder, change both.

| # | Name | Configurator profile / PHY | Channels | Notes |
|---|---|---|---|---|
| 0 | `wmbus_tc` | Mbus → *WMbus TC M2O (100k, frameA)* | ch 0 = 868.950 MHz | `RAIL_WMBUS_Config(rail,true)` enables T + C frame A + C frame B RX; mode per packet in `subPhyId` (0 = T/A, 2 = C/A, 3 = C/B) |
| 1 | `wmbus_s` | Mbus → *WMbus S (32.768k, Manchester)* | ch 0 = 868.300 MHz | S1 meters |
| 2 | `wmbus_cb` | Mbus → *WMbus C M2O frameB (100k)* | ch 0 = 868.950 MHz | Fallback: the single-mode C PHY selects the frame type by sync word and cannot receive A and B at once ([Silicon Labs limitations](https://docs.silabs.com/rail/latest/rail-wmbus-apps-with-efr32/02-limitations)). Use it if #0 does not deliver frame B on this part |
| 3 | `154g` | IEEE 802.15.4 SUN FSK (or Wi-SUN FAN 1.0) → 2-FSK, 50 kbps, mod. index 1.0, 2-byte PHR, FCS 16/32 per PHR | ch 0 = 863.100 MHz, 100 kHz spacing, 70 channels | Assumption: Wi-SUN FAN 1.0 EU PHY mode 1b / 802.15.4g SUN-FSK operating mode #1 for the 863–870 MHz band. RAIL `RAIL_IEEE802154_Init` with `promiscuousMode = true` and `RAIL_IEEE802154_G_OPTION_GB868` for PHR handling |
| 4–9 | `fsk2k4 … fsk100k` | Base (custom) → 2-GFSK, BT 0.5, preamble 32 bits `0xAA…`, sync `0x2DD4`/16, 1-byte length header, variable length, CRC-16 CCITT | base 863.000 MHz, 25 kHz, ch 0–280 | Deviation/BW: 2.4 k: ±2.4 k/25 k · 4.8 k: ±2.4 k/25 k · 9.6 k: ±4.8 k/25 k · 38.4 k: ±20 k/100 k · 50 k: ±25 k/150 k · 100 k: ±50 k/250 k. Sync word, fixed length and CRC acceptance change at runtime (`Y`, `L`, `K`) |

Fine tuning inside a grid uses `RAIL_SetFreqOffset` (±16383 ticks).

## Commands

Same syntax on the VCOM terminal and on cmdlink (Pi GPIO14 → PA06, 115200
8N1). One letter, argument, CR/LF. Replies and status lines start with `#`.

```
P<name|0-9>  select PHY (P? lists)          F<kHz>        tune, e.g. F868950
H<kHz>,<kHz>,...;<dwell_ms>  hop list       H0            hopping off
Y<hex>,<bits>  sync word (fsk*)             L<n>          fixed length, 0 = variable (fsk*)
K0|K1        drop|keep CRC-failed frames    A<off>,<hex>  address filter (A clears)
N<min>,<max> length filter                  Ot|Or|Ob|Os   output text | rtl_wmbus | PKT1 on UART | PKT1 on SPI
D<unix_s>    wall clock for rtl_wmbus       s / R / v / ? stats / reset / version / help
```

Defaults after reset: `wmbus_tc`, 868.950 MHz, text output on VCOM, CRC-failed
frames dropped. Build with `-DSNIFFER_DEFAULT_SPI=1` to boot into `Os`
(headless rev B radio 2).

## Output formats

### Text (`Ot`)

One line per packet, `;`-separated, hex payload last:

```
P;<pkt_seq>;<ts_us>;<phy>;<subphy>;<freq_hz>;<rssi_dbm>;<lqi>;<crc_ok>;<drops>;<HEX>
P;17;123456789;wmbus_tc;0;868950000;-71;213;1;0;3444EE4D813929271608811D7A51...
```

`ts_us` is the RAIL timestamp of the sync-word end (32-bit µs, wraps every
71 min). The payload is the raw RAIL packet: for wM-Bus possibly with the
per-block DLL CRCs (the host detects and strips them), for 802.15.4g with
the 2-byte PHR first and the FCS last.

### rtl_wmbus (`Or`)

Exactly what `wmbusmeters` expects from `rtl_wmbus` (verified against
`wmbusmeters/src/wmbus_rtlwmbus.cc`):

```
MODE;CRC_OK;3OUTOF6OK;YYYY-mm-dd HH:MM:SS.mmm;PACKET_RSSI;CURRENT_RSSI;ID;0x<DATAGRAM WITHOUT CRC BYTES>
T1;1;1;2026-11-07 21:04:11.512;-71;-71;27293981;0x3444EE4D8139292716087A51000000046D...
```

- `MODE` is `T1`, `C1` or `S1` (from `subPhyId` on `wmbus_tc`).
- wmbusmeters requires characters 3 and 5 to be `1`; a CRC-failed frame is
  emitted with `0` and is rejected by wmbusmeters, which is the intent.
- The timestamp must parse with `%Y-%m-%d %H:%M:%S`. The FG23 has no RTC: set
  the clock with `D<unix_seconds>` (sniffd does this at start-up); otherwise
  it counts from 1970-01-01, which wmbusmeters accepts but logs as such.
- RSSI is integer dBm; wmbusmeters reads the field with `atof()` when it is
  shorter than 5 characters, so `-100` still works.
- The datagram has the DLL CRCs removed and `L` rewritten (frame B is
  converted to the frame A `L` convention, as wmbusmeters expects).

### Binary PKT1 (`Ob`, `Os`)

Defined in `pktlink.h`: 1040-byte blocks, 16-byte header (`'PKT1'`, block
seq, bytes used, flags, centre frequency) + up to 1000 bytes of records.
Each record: 24-byte header (len, phy, subphy, flags, rssi, lqi, hop_idx,
ts_us, pkt_seq, freq_hz, channel, drops) + PSDU, padded to 4 bytes. One
block holds several short packets; a block is sent as soon as the transport
is idle. The host side (`pktlink.py`, `libionos`) demultiplexes `IQB2`,
`SPC1` and `PKT1` on the magic, so the Pi DMA path is shared with the I/Q
firmware.

SPI transport (mode A of the repository README): USART0 secondary on
PC05 (SCLK in), PC00 (MISO out), PA07 (CS in), PA05 (MOSI in, unused);
DRDY on PD02 goes high when a block is loaded into the LDMA and low after
the Pi has clocked 1040 bytes and released CS. Start the Pi at 4 MHz SCLK;
the maximum is measurement M1.

## To verify (could not be checked without hardware / SDK)

1. **Radio configurator PHY names** for wM-Bus S, C frame B and the SUN-FSK
   entry (the TC name `PHY_wMbus_ModeTC_M2O_100k_frameA` is from the RAIL
   docs). The radioconf may need to be recreated by hand.
2. **`channelConfigs[]` order** equals the protocol order in the
   radioconf. Check `autogen/rail_config.h` after generation.
3. **wM-Bus payload contents**: whether RAIL delivers the frame with or
   without the per-block DLL CRCs on Series 2. Both cases are handled
   (`PKT_F_WMBUS_CRCS` flag; `wmbus_strip_crcs()`), but only one will occur.
4. **`RAIL_WMBUS_Config(rail, true)` and frame B**: the RAIL API page says
   the TC PHY then receives C frame B too; the application-note limitation
   page says A and B need separate configs. `wmbus_cb` is the fallback.
5. **802.15.4g**: whether `RAIL_IEEE802154_G_OPTION_GB868` is the right
   option for the PHR on a configurator SUN-FSK PHY, and whether RAIL hands
   over the PHR bytes in the packet (`PKT_F_HAS_PHR` is set assuming yes).
6. **USART0 secondary-mode** routing (`CSROUTE`, `CSPEN`), LDMA `TXBL`
   behaviour before the first clock, and completion detection (LDMA done +
   CS high). Maximum SCLK: M1.
7. **`RAIL_SetFreqOffset` tick size** at 868 MHz (`TUNE_TICK_HZ`, 4.6492 Hz
   assumed from the 434 MHz measurement).
8. **`RAIL_GetRxTimeSyncWordEndAlt`** availability in RAIL 2.19 (otherwise
   use `RAIL_GetRxTimeSyncWordEnd` on `timeReceived.packetTime`).
9. Component ids in the `.slcp` (`dmadrv`, `emlib_ldma`, `rail_util_protocol`)
   and the `-std=c99` toolchain option key.
10. Hopping: `RAIL_CHANNEL_HOPPING_BUFFER_SIZE_PER_CHANNEL` buffer sizing and
   that `RAIL_RxPacketDetails_t::channel` reports the hop channel.
