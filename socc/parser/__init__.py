"""Parser module."""

from typing import Dict, Any
from socc.model import SoC, Regulator, Clock, ClockProvider, IRNode
from socc.model.base import IRNode as IRNodeClass
from socc.parser.dts_parser import parse_dts
from socc.parser.dts_mapper import dts_to_soc


def build_sample_model(soc_name: str = "rk3588") -> SoC:
    """Build a sample SoC model for demo and testing.

    Args:
        soc_name: SoC name string.

    Returns:
        Populated SoC data model.
    """
    model = SoC(name=soc_name)

    # --- power tree ---
    sys_pwr = Regulator(
        name="sys_pwr",
        type="fixed",
        voltage_min=5.0,
        voltage_max=5.0,
    )
    dcdc1 = Regulator(
        name="dcdc1",
        type="dcdc",
        voltage_min=0.8,
        voltage_max=1.1,
        parent="sys_pwr",
    )
    ldo1 = Regulator(
        name="ldo1",
        type="ldo",
        voltage_min=3.0,
        voltage_max=3.3,
        parent="sys_pwr",
    )

    model.power_tree.add_regulator(sys_pwr)
    model.power_tree.add_regulator(dcdc1)
    model.power_tree.add_regulator(ldo1)

    # supply edges
    model.power_tree.add_edge("sys_pwr", "dcdc1")
    model.power_tree.add_edge("sys_pwr", "ldo1")

    # --- clock tree ---
    cru = ClockProvider(
        name="cru",
        type="cru",
        base_addr=0xff760000,
        outputs=["apll", "dpll", "clk_i2c0", "clk_uart0"],
    )
    model.clock_tree.add_provider(cru)

    # clocks
    root_clock = Clock(
        name="osc_24m",
        rate=24e6,  # 24 MHz
        provider="external",
    )
    apll = Clock(
        name="apll",
        rate=2.4e9,  # 2.4 GHz
        provider="cru",
        parent="osc_24m",
    )
    clk_i2c0 = Clock(
        name="clk_i2c0",
        rate=100e3,  # 100 kHz
        provider="cru",
        parent="apll",
    )
    clk_uart0 = Clock(
        name="clk_uart0",
        rate=115200,
        provider="cru",
        parent="apll",
    )

    model.clock_tree.add_clock(root_clock)
    model.clock_tree.add_clock(apll)
    model.clock_tree.add_clock(clk_i2c0)
    model.clock_tree.add_clock(clk_uart0)

    # --- devices ---
    # I2C0
    i2c0_node = IRNodeClass(
        name="i2c0",
        path="/soc/i2c0@fac0000",
        properties={
            "compatible": "rockchip,rk3588-i2c",
            "reg": "0xfac0000 0x1000",
        },
    )
    model.devices["i2c0"] = i2c0_node
    model.device_supplies["i2c0"] = ["ldo1"]
    model.device_clocks["i2c0"] = ["clk_i2c0"]

    # UART0
    uart0_node = IRNodeClass(
        name="uart0",
        path="/soc/uart0@fe660000",
        properties={
            "compatible": "rockchip,rk3588-uart",
            "reg": "0xfe660000 0x1000",
        },
    )
    model.devices["uart0"] = uart0_node
    model.device_supplies["uart0"] = ["ldo1"]
    model.device_clocks["uart0"] = ["clk_uart0"]

    # CPU
    cpu_node = IRNodeClass(
        name="cpu",
        path="/cpus/cpu@0",
        properties={
            "compatible": "arm,cortex-a76",
        },
    )
    model.devices["cpu"] = cpu_node
    model.device_supplies["cpu"] = ["dcdc1"]

    return model


def parse_dts_file(dts_file_path: str, soc_name: str = "unknown") -> SoC:
    """Parse a DTS file and return a SoC model.

    Args:
        dts_file_path: Path to the DTS file.
        soc_name: SoC name used for constraint matching.

    Returns:
        Populated SoC data model.

    Raises:
        FileNotFoundError: If the file does not exist.
        SyntaxError: On DTS syntax errors.
    """
    with open(dts_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # parse DTS text
    dts_tree = parse_dts(content)
    
    # map to SoC model
    soc = dts_to_soc(dts_tree, soc_name)
    
    return soc


__all__ = ['build_sample_model', 'parse_dts', 'dts_to_soc', 'parse_dts_file']

__all__ = ["build_sample_model"]
