#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
#
# One-command Raspberry Pi setup for the ionos-maximus host stack:
#   - apt packages (python, spidev/gpiod, cmake, SoapySDR dev)
#   - /boot/firmware/config.txt: SPI0 (1 or 2 CS), PL011 on GPIO14 for cmdlink
#   - python venv in /opt/ionos/venv with ionos_tools (sniffd)
#   - libionos + SoapySDR module built and installed
#   - systemd unit ionos-sniffd.service + /etc/default/ionos-sniffd
#   - example wmbusmeters config (not enabled; install wmbusmeters separately)
#
#   sudo ./install.sh [--rev a|b] [--no-soapy] [--no-boot]
set -eu

REV=a
SOAPY=1
BOOT=1
while [ $# -gt 0 ]; do
  case "$1" in
    --rev) REV="$2"; shift ;;
    --no-soapy) SOAPY=0 ;;
    --no-boot) BOOT=0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
HERE=$(cd "$(dirname "$0")" && pwd)
[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }

echo "== apt"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip python3-dev cmake g++ pkg-config \
  libgpiod-dev gpiod python3-libgpiod python3-spidev
if [ "$SOAPY" = 1 ]; then
  apt-get install -y -qq libsoapysdr-dev soapysdr-tools
fi

if [ "$BOOT" = 1 ]; then
  CFG=/boot/firmware/config.txt
  [ -f "$CFG" ] || CFG=/boot/config.txt
  echo "== $CFG"
  add_line() { grep -qxF "$1" "$CFG" || echo "$1" >> "$CFG"; }
  add_line "# ionos-maximus (README pin map, link mode A)"
  if [ "$REV" = b ]; then
    add_line "dtoverlay=spi0-2cs"
  else
    add_line "dtoverlay=spi0-1cs"
  fi
  add_line "enable_uart=1"
  add_line "dtoverlay=disable-bt"          # PL011 on GPIO14/15 for cmdlink (and rev B UART RX)
  add_line "dtparam=i2c_arm=on"           # radio-board ID EEPROM / HAT EEPROM
  systemctl disable --now hciuart 2>/dev/null || true
  # the serial console must not own ttyAMA0
  if [ -f /boot/firmware/cmdline.txt ]; then
    sed -i 's/ console=serial0,115200//; s/ console=ttyAMA0,115200//' /boot/firmware/cmdline.txt
  fi
fi

echo "== python venv"
mkdir -p /opt/ionos
python3 -m venv --system-site-packages /opt/ionos/venv
/opt/ionos/venv/bin/pip install -q --upgrade pip
/opt/ionos/venv/bin/pip install -q "$HERE"

echo "== libionos"
cmake -S "$HERE/libionos" -B /opt/ionos/build-libionos -DCMAKE_BUILD_TYPE=Release -DIONOS_BUILD_TESTS=OFF > /dev/null
cmake --build /opt/ionos/build-libionos -j"$(nproc)" > /dev/null
cmake --install /opt/ionos/build-libionos > /dev/null
ldconfig

if [ "$SOAPY" = 1 ]; then
  echo "== SoapySDR module"
  cmake -S "$HERE/soapy" -B /opt/ionos/build-soapy -DCMAKE_BUILD_TYPE=Release > /dev/null
  cmake --build /opt/ionos/build-soapy -j"$(nproc)" > /dev/null
  cmake --install /opt/ionos/build-soapy > /dev/null
  SoapySDRUtil --info | grep -i ionos || echo "(module installed; SoapySDRUtil --find=driver=ionos once the HAT is present)"
fi

echo "== systemd"
install -m 644 "$HERE/systemd/ionos-sniffd.service" /etc/systemd/system/
[ -f /etc/default/ionos-sniffd ] || install -m 644 "$HERE/etc/ionos-sniffd.default" /etc/default/ionos-sniffd
if [ "$REV" = b ]; then
  sed -i 's#^SNIFFD_SOURCE=.*#SNIFFD_SOURCE=spi:/dev/spidev0.1#; s#^SNIFFD_EXTRA=.*#SNIFFD_EXTRA=--drdy gpiochip0:4#' /etc/default/ionos-sniffd
fi
systemctl daemon-reload
systemctl enable ionos-sniffd.service

echo "== wmbusmeters example"
mkdir -p /opt/ionos/examples
install -m 644 "$HERE/etc/wmbusmeters.conf" /opt/ionos/examples/wmbusmeters.conf
install -m 644 "$HERE/etc/wmbusmeters.d-example" /opt/ionos/examples/wmbusmeters.d-MyWater

cat <<MSG

Done. Next:
  1. reboot (SPI / UART overlays)
  2. flash firmware/sniffer onto the FG23 (Simplicity Studio or OpenOCD over SWD)
  3. systemctl start ionos-sniffd; journalctl -fu ionos-sniffd
  4. wmbusmeters: install it (https://github.com/wmbusmeters/wmbusmeters), copy
     /opt/ionos/examples/wmbusmeters.conf to /etc/wmbusmeters.conf and the meter
     file to /etc/wmbusmeters.d/, then systemctl restart wmbusmeters
MSG
