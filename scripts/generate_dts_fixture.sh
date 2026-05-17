#!/usr/bin/env bash
# Generate data/examples/rk3588s-orangepi-5.dts from mainline Linux source.
#
# Requirements: curl, clang, dtc
#
# The output is a self-contained flat DTS file that does not require any
# preprocessor step to be read by the socc parser.  It exactly represents
# the OrangePi 5 board DTS as found in arch/arm64/boot/dts/rockchip/ in
# the mainline Linux kernel (torvalds/linux, master branch).

set -euo pipefail

KERNEL_BASE="https://raw.githubusercontent.com/torvalds/linux/master"
ROCKCHIP="$KERNEL_BASE/arch/arm64/boot/dts/rockchip"
BINDINGS="$KERNEL_BASE/include/dt-bindings"
UAPI="$KERNEL_BASE/include/uapi/linux"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "[1/4] Downloading DTS source files..."
curl -sL "$ROCKCHIP/rk3588s-orangepi-5.dtsi" -o "$WORK/rk3588s-orangepi-5.dtsi"
curl -sL "$ROCKCHIP/rk3588s.dtsi"             -o "$WORK/rk3588s.dtsi"
curl -sL "$ROCKCHIP/rk3588-base.dtsi"         -o "$WORK/rk3588-base.dtsi"
curl -sL "$ROCKCHIP/rk3588-opp.dtsi"          -o "$WORK/rk3588-opp.dtsi"
curl -sL "$ROCKCHIP/rk3588-base-pinctrl.dtsi" -o "$WORK/rk3588-base-pinctrl.dtsi"
curl -sL "$ROCKCHIP/rockchip-pinconf.dtsi"    -o "$WORK/rockchip-pinconf.dtsi"

echo "[2/4] Downloading dt-bindings headers..."
H="$WORK/dt-bindings"
mkdir -p "$H/gpio" "$H/leds" "$H/input" "$H/pinctrl" "$H/soc" "$H/usb" \
         "$H/interrupt-controller" "$H/clock" "$H/power" "$H/phy" \
         "$H/reset" "$H/ata" "$H/thermal"

curl -sL "$BINDINGS/gpio/gpio.h"                           -o "$H/gpio/gpio.h"
curl -sL "$BINDINGS/leds/common.h"                         -o "$H/leds/common.h"
curl -sL "$BINDINGS/input/input.h"                         -o "$H/input/input.h"
curl -sL "$UAPI/input-event-codes.h"                       -o "$H/input/linux-event-codes.h"
curl -sL "$BINDINGS/pinctrl/rockchip.h"                    -o "$H/pinctrl/rockchip.h"
curl -sL "$BINDINGS/soc/rockchip,vop2.h"                   -o "$H/soc/rockchip,vop2.h"
curl -sL "$BINDINGS/usb/pd.h"                              -o "$H/usb/pd.h"
curl -sL "$BINDINGS/clock/rockchip,rk3588-cru.h"           -o "$H/clock/rockchip,rk3588-cru.h"
curl -sL "$BINDINGS/interrupt-controller/arm-gic.h"        -o "$H/interrupt-controller/arm-gic.h"
curl -sL "$BINDINGS/interrupt-controller/irq.h"            -o "$H/interrupt-controller/irq.h"
curl -sL "$BINDINGS/phy/phy.h"                             -o "$H/phy/phy.h"
curl -sL "$BINDINGS/power/rk3588-power.h"                  -o "$H/power/rk3588-power.h"
curl -sL "$BINDINGS/reset/rockchip,rk3588-cru.h"           -o "$H/reset/rockchip,rk3588-cru.h"
curl -sL "$BINDINGS/ata/ahci.h"                            -o "$H/ata/ahci.h"
curl -sL "$BINDINGS/thermal/thermal.h"                     -o "$H/thermal/thermal.h"

echo "[3/4] Preprocessing and compiling..."
clang -E -nostdinc -undef -x assembler-with-cpp \
    -I "$WORK" \
    "$WORK/rk3588s-orangepi-5.dtsi" 2>/dev/null \
    | grep -v '^#' \
    | sed 's/(~0)/0xffffffff/g' \
    > "$WORK/flat.dts"

dtc -I dts -O dtb -f "$WORK/flat.dts" -o "$WORK/board.dtb" 2>/dev/null
dtc -I dtb -O dts "$WORK/board.dtb" -o "$WORK/clean.dts" 2>/dev/null

echo "[4/4] Installing fixture..."
mkdir -p data/examples
cp "$WORK/clean.dts" data/examples/rk3588s-orangepi-5.dts
echo "Fixture written to data/examples/rk3588s-orangepi-5.dts"
wc -l data/examples/rk3588s-orangepi-5.dts
