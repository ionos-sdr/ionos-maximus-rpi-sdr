# Raspberry Pi host stack

Software that runs on the Raspberry Pi next to the FG23 (rev A: one radio;
rev B: radio 1 = I/Q, radio 2 = sniffer). Two data paths share one link
format (1040-byte blocks, demultiplexed on the magic `IQB2` / `SPC1` /
`PKT1`):

```
 FG23 sniffer firmware ──UART/SPI──► sniffd ──► rtl_wmbus lines ─► wmbusmeters ─► MQTT / Home Assistant
   (firmware/sniffer)                        ├─► pcap (802.15.4 TAP / USER0 + TLVs) ─► Wireshark, Sniff-Sight
                                             ├─► JSON lines (TCP / Unix socket)      ─► Sniff-Sight PacketSource, scripts
                                             └─► ACC-based missed-telegram summary (PER per meter, no keys)

 FG23 I/Q firmware ──SPI0 + DRDY──► libionos (C) ──► SoapySDR module ──► SDR++, GNU Radio, rtl_433 -d driver=ionos
   (ionos-sdr iq_capture)                     └─► ionos_cat (raw int16 I/Q to stdout)
```

Status: written and tested here against replay data and public test
telegrams; **not yet run against hardware**. Limits that depend on the
link (sample rate, SPI clock) are "to be measured" (README M1–M5).

| Directory | Content | Build/test here |
|---|---|---|
| `ionos_tools/` | Python package: `pktlink.py`, `wmbus.py`, `pcapout.py`, `sniffd.py` | `pytest` – 14 tests pass |
| `libionos/` | C99 library + `ionos_cat`, replay and spidev/gpiod backends | CMake, `ctest` – 3 tests pass |
| `soapy/` | SoapySDR module `driver=ionos` | builds; `SoapySDRUtil --find/--probe/--rate` work on a replay file |
| `systemd/`, `etc/` | `ionos-sniffd.service`, defaults, wmbusmeters example | |
| `install.sh` | one-command Pi setup | shell-checked only |

## Install on the Pi

```sh
git clone https://github.com/ionos-sdr/ionos-maximus-rpi-sdr
cd ionos-maximus-rpi-sdr/host
sudo ./install.sh            # --rev b for the two-radio board, --no-soapy to skip SoapySDR
sudo reboot
```

`install.sh` adds to `/boot/firmware/config.txt`: `dtoverlay=spi0-1cs`
(`spi0-2cs` for rev B), `enable_uart=1`, `dtoverlay=disable-bt` (PL011 on
GPIO14 = cmdlink TX, GPIO15 = optional rev B UART RX), `dtparam=i2c_arm=on`;
removes the serial console from `cmdline.txt`. It creates
`/opt/ionos/venv` with `sniffd`, installs `libionos`, `ionos_cat`, the
SoapySDR module, and enables `ionos-sniffd.service` configured by
`/etc/default/ionos-sniffd`.

Flashing the FG23 from the Pi (SWD on GPIO23/24, RESETn GPIO22) is
described in the repository README; the firmware is `firmware/sniffer`.

## sniffd

```
sniffd --source serial:/dev/ttyACM0 --phy wmbus_tc --rtlwmbus -          # bench, WSTK USB CDC
sniffd --source spi:/dev/spidev0.0 --drdy gpiochip0:25 --cmd /dev/ttyAMA0 \
       --phy wmbus_tc --rtlwmbus /run/ionos/wmbus.fifo --jsonl 127.0.0.1:7771 \
       --acc-summary /var/lib/ionos/acc_summary.json                       # rev A on the HAT
sniffd --source spi:/dev/spidev0.1 --drdy gpiochip0:4 --cmd /dev/ttyAMA0 \
       --phy 154g --freq 868100 --pcap /var/log/ionos/154g.pcap            # rev B radio 2
sniffd --source file:capture.bin --exit-on-eof --text                      # replay
```

At start it sends `D<unix time>`, `P<phy>`, `F<kHz>`, `H...`, `K1` and the
output mode (`Os` for SPI, `Ot`/`Ob` for serial) over `--cmd` (cmdlink
UART) or, without `--cmd`, over the same serial port. Sources: `serial:`
(pyserial, text or PKT1 auto-detected), `spi:` (spidev + libgpiod DRDY
edge, `--drdy none` polls), `file:` / `-`.

### Output formats

**rtl_wmbus lines** (`--rtlwmbus`): the format wmbusmeters' `rtlwmbus`
input parses (verified against `src/wmbus_rtlwmbus.cc`):

```
MODE;CRC_OK;3OUTOF6OK;YYYY-mm-dd HH:MM:SS.mmm;PACKET_RSSI;CURRENT_RSSI;ID;0x<datagram without DLL CRCs>
T1;1;1;2026-11-07 21:04:11.512;-71;-71;27293981;0x3444ee4d8139292716087a51000000046d1912...
```

`MODE` = `T1`/`C1`/`S1` (C frame B is reported as `C1`), timestamp in
**UTC** (wmbusmeters applies `timegm`), RSSI in dBm, `ID` = A-field,
datagram with per-block CRCs removed and `L` rewritten (frame B → frame-A
`L` convention). sniffd uses the host clock and detects whether the FG23
delivered the frame with or without CRCs.

**pcap** (`--pcap`): DLT 283 (IEEE 802.15.4 TAP) when `--phy 154g`, else
DLT 147 (`LINKTYPE_USER0`) with the **same TAP header and TLVs**: FCS type
(0), RSS float dBm (1), bit rate (2), start-of-frame timestamp ns (5, device
RAIL time), LQI (10), channel frequency kHz (11), and the private TLV
`0x8000` = `u8 phy, u8 subphy, u8 flags, u8 hop_idx, u32 pkt_seq, u32
dev_ts_us, u32 drops`. Record timestamps are host time. For 802.15.4g the
2-byte PHR is stripped and the FCS kept (FCS-type TLV says 16/32 bit), so
Wireshark decodes the frames directly. In Wireshark map DLT 147 to a
dissector via *Preferences → Protocols → DLT_USER*. Reader:
`ionos_tools.pcapout.parse_tap_record`. Sniff-Sight's `spinel_sniffer.py`
produces the same TAP layout on DLT 283, so a future `PacketSource` can
consume both.

**JSON lines** (`--jsonl host:port` or `unix:/path`), one object per packet:

```json
{"t":1762549451.512,"dev_ts_us":123456,"seq":17,"phy":"wmbus_tc","subphy":0,"mode":"T1",
 "freq_mhz":868.95,"channel":0,"rssi":-71,"lqi":213,"crc_ok":true,"flags":17,"drops":0,
 "len":61,"hex":"3444ee4d...","src":"SON 27293981","manufacturer":"SON","id":"27293981",
 "version":22,"type":8,"c":68,"ci":122,"acc":81}
```

**ACC summary** (`--acc-summary`): per meter `received`, `missed`,
`duplicates`, `acc_resets`, `per`, RSSI min/mean/max, modes seen. The
access number (ACC) in the short/long TPL header or the ELL increments
once per telegram, so gaps count missed telegrams without any key.

### Text and binary wire formats

Defined in `firmware/sniffer/README.md` and `firmware/sniffer/pktlink.h`
(a byte-identical copy is `libionos/include/pktlink.h`; `ctest` checks
it). Text: `P;seq;ts_us;phy;subphy;freq_hz;rssi;lqi;crc_ok;drops;HEX`.
Binary: 1040-byte `PKT1` blocks, 24-byte record headers.

## wmbusmeters and Home Assistant

1. Install wmbusmeters (Debian package or from source).
2. `/etc/wmbusmeters.conf` (example in `etc/wmbusmeters.conf`):
   ```
   device=rtlwmbus:CMD(cat /run/ionos/wmbus.fifo)
   format=json
   shell=/usr/bin/mosquitto_pub -h localhost -t "wmbusmeters/$METER_ID" -m "$METER_JSON"
   ```
   `ionos-sniffd.service` creates the FIFO and keeps it open. For the bench
   (WSTK USB CDC, no HAT): `device=rtlwmbus:CMD(/opt/ionos/venv/bin/sniffd
   --source serial:/dev/ttyACM0 --phy wmbus_tc --rtlwmbus -)`.
3. One file per meter in `/etc/wmbusmeters.d/` (`etc/wmbusmeters.d-example`).
4. Home Assistant: MQTT integration, sensors on `wmbusmeters/<id>` with
   `value_template: "{{ value_json.total_m3 }}"` etc.; or run the
   wmbusmeters Home Assistant add-on and point it at the FIFO.

## libionos and SoapySDR (I/Q path, mode A)

```c
ionos_config_t cfg; ionos_config_init(&cfg);        /* spi, /dev/spidev0.0, 4 MHz, gpiochip0:25 */
cfg.cmd_path = "/dev/ttyAMA0";
ionos_dev_t *dev; ionos_open(&cfg, &dev);
ionos_command(dev, "F868950"); ionos_command(dev, "i32");
ionos_start(dev);
int n = ionos_read_iq(dev, buf, 4096, 500);         /* int16 I,Q pairs */
ionos_stats_t st; ionos_stats(dev, &st);            /* lost_blocks must stay 0 (M4) */
ionos_close(dev);
```

Backends: `spi` (spidev, DRDY via libgpiod v1 edge events; polls without
gpiod) and `replay` (file of blocks, `ionos_cat -b replay -p file -r`).
PKT1/SPC1 blocks on the same link go to callbacks.

SoapySDR: `SoapySDRUtil --probe="driver=ionos,cmd=/dev/ttyAMA0"`; args
`path`, `backend`, `spi_hz`, `drdy`, `cmd`, `realtime`, `loop`. One RX
channel, CS16 native, CF32 converted. Sample rates are discrete
(`fs_in / R`, R = 8…4096 step 8, fs_in = 1 Msps on the 270 kHz PHY),
advertised range up to 500 ksps **(to be measured: M1 SPI clock, M4
sustained rate, M5 CPU)**; bandwidth ≤ 270 kHz; frequency 100–1000 MHz
(BRD4265B matching is 868/915 MHz). Frequency and decimation are sent to
the FG23 as `F<kHz>` and `i<R>` when `cmd=` is given. Block loss shows as
`lost_blocks` / `ring_overflows` in `readSetting` and as warnings.

rtl_433 can use the device through SoapySDR: `rtl_433 -d driver=ionos -f
868.3M -s 250k` (rtl_433 selects the nearest listed rate; keep it ≤ the
measured ceiling). SDR++: SoapySDR source, device `ionos`.

## Limits and to verify

- **Not run on hardware.** Serial/text path, PKT1 path, pcap, rtl_wmbus
  lines, ACC tracking, libionos replay and the SoapySDR module are tested
  with synthetic blocks and public telegrams only.
- SPI secondary clock (M1), sustained rate (M4) and CPU load (M5) decide
  the real I/Q ceiling; sniffd's packet path needs only a few kB/s.
- libgpiod: the C backend uses the v1 API (1.6, Raspberry Pi OS bookworm);
  v2 falls back to polling. sniffd supports both Python bindings.
- `spidev` transfers are 1040 bytes, below the default `bufsiz` (4096).
- Whether RAIL delivers wM-Bus frames with or without DLL CRCs is handled
  either way; whether 802.15.4g frames arrive with the PHR is assumed yes
  (`PKT_F_HAS_PHR`), see the firmware README.
- `wmbusmeters` interprets the rtl_wmbus timestamp as UTC (`timegm`); we
  emit UTC. rtl_wmbus itself prints local time, so logs differ by the
  timezone offset from a real rtl_wmbus setup.

## Licenses

MIT (`../LICENSE`). Test telegrams in `tests/` are public test data from
the wmbusmeters project; no wmbusmeters, rtl_wmbus or Sniff-Sight code is
included.
