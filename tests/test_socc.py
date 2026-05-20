"""Consolidated test suite for soc-consistency (socc).

Replaces the previous collection of per-version test files.  The goal is to
lock down the most credibility-critical behaviour in one maintainable place:

  1. Core model          — PowerTree / ClockTree data structures
  2. DTS parser          — tokeniser, parser, mapper
  3. Mainline integration— full pipeline against a real kernel DTS (RK3588s)
  4. NXP vendor rules    — IMX-001, IMX-002
  5. Common rules        — PIN-301, COMP-101, PWR-101, PWR-102
  6. IRQ / bounds        — IRQ collision, GPIO/DMA bounds
  7. Simulation          — PS-001/002/003, CG-001/002, RS-001/002
  8. Regression FP       — false-positive fixes locked in (DMA-001, CK-107, PD-006 …)

Run with:  python -m pytest tests/test_socc.py -v
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ─── shared fixture path ──────────────────────────────────────────────────────

MAINLINE_DTS = Path(__file__).parent.parent / "data" / "examples" / "rk3588s-orangepi-5.dts"

# ─── model helpers ────────────────────────────────────────────────────────────

from socc.model import (
    IRNode, SoC, Regulator, PowerTree, Clock, ClockTree, ClockProvider,
)
from socc.rules import RuleRegistry, CheckContext


def _irnode(name: str, path: str = None, props: Optional[Dict] = None) -> IRNode:
    return IRNode(name=name, path=path or f"/{name}",
                  properties=props or {}, children=[], parent=None)


def _reg(
    name: str, parent: Optional[str] = None,
    startup_us: int = 0, ramp_us: int = 0,
    v_min: float = 1.0, v_max: float = 3.3,
    seq: int = 0,
) -> Regulator:
    return Regulator(
        name=name, type="ldo",
        voltage_min=v_min, voltage_max=v_max,
        consumers=[], parent=parent,
        startup_delay_us=startup_us, ramp_delay_us=ramp_us,
        max_current_ma=500, sequence_order=seq,
    )


def _clk(name: str, parent: Optional[str] = None,
         consumers: Optional[List[str]] = None) -> Clock:
    return Clock(name=name, rate=100_000_000, provider="pll",
                 parent=parent, consumers=consumers or [])


def _simple_power_tree() -> PowerTree:
    pt = PowerTree()
    pt.nodes["vcc_5v0"] = _reg("vcc_5v0")
    pt.nodes["vcc_3v3"] = _reg("vcc_3v3", parent="vcc_5v0")
    pt.nodes["vcc_1v8"] = _reg("vcc_1v8", parent="vcc_3v3")
    return pt


def _simple_clock_tree() -> ClockTree:
    ct = ClockTree()
    ct.clocks["xtal_24m"]  = _clk("xtal_24m")
    ct.clocks["pll_gpll"]  = _clk("pll_gpll",  parent="xtal_24m")
    ct.clocks["clk_uart0"] = _clk("clk_uart0", parent="pll_gpll", consumers=["uart0"])
    ct.clocks["clk_i2c0"]  = _clk("clk_i2c0",  parent="pll_gpll", consumers=["i2c0"])
    return ct


def _make_soc_v07(**kwargs) -> SoC:
    defaults = dict(name="test-soc", power_tree=PowerTree(), clock_tree=ClockTree(),
                    devices={}, device_supplies={}, device_clocks={})
    defaults.update(kwargs)
    return SoC(**defaults)


def _ctx(soc_name: str = "test-soc") -> CheckContext:
    return CheckContext(soc_name=soc_name)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CORE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

from socc.model import IRNode, SoC, Regulator, PowerTree, Clock, ClockTree, ClockProvider
from socc.rules.rockchip import register_rockchip_rules
from socc.rules.common import register_common_rules
from socc.engine import Checker
from socc.parser import build_sample_model


class TestPowerTree:
    def test_add_regulator(self):
        tree = PowerTree()
        tree.add_regulator(Regulator(name="vdd", type="fixed", voltage_min=3.3, voltage_max=3.3))
        assert "vdd" in tree.nodes

    def test_duplicate_regulator(self):
        tree = PowerTree()
        reg = Regulator(name="vdd", type="fixed", voltage_min=3.3, voltage_max=3.3)
        tree.add_regulator(reg)
        with pytest.raises(ValueError):
            tree.add_regulator(reg)

    def test_cycle_detection(self):
        tree = PowerTree()
        a = Regulator(name="a", type="fixed", voltage_min=1.0, voltage_max=1.0)
        b = Regulator(name="b", type="fixed", voltage_min=1.0, voltage_max=1.0)
        tree.add_regulator(a)
        tree.add_regulator(b)
        tree.add_edge("a", "b")
        tree.add_edge("b", "a")
        assert len(tree.detect_cycles()) > 0

    def test_orphaned_detection(self):
        tree = PowerTree()
        reg1 = Regulator(name="used", type="fixed", voltage_min=3.3, voltage_max=3.3)
        reg2 = Regulator(name="unused", type="fixed", voltage_min=3.3, voltage_max=3.3)
        tree.add_regulator(reg1)
        tree.add_regulator(reg2)
        reg1.consumers = ["device1"]
        orphans = tree.get_all_orphaned()
        assert "unused" in orphans
        assert "used" not in orphans


class TestClockTree:
    def test_add_clock(self):
        tree = ClockTree()
        tree.add_clock(Clock(name="clk_test", rate=1e6, provider="cru"))
        assert "clk_test" in tree.clocks

    def test_duplicate_clock(self):
        tree = ClockTree()
        clk = Clock(name="clk_test", rate=1e6, provider="cru")
        tree.add_clock(clk)
        with pytest.raises(ValueError):
            tree.add_clock(clk)

    def test_find_path_to_root(self):
        tree = ClockTree()
        tree.add_clock(Clock(name="osc", rate=24e6, provider="external"))
        tree.add_clock(Clock(name="pll", rate=1e9, provider="cru", parent="osc"))
        tree.add_clock(Clock(name="sys_clk", rate=500e6, provider="cru", parent="pll"))
        assert tree.find_path_to_root("sys_clk") == ["sys_clk", "pll", "osc"]

    def test_cycle_detection_in_clock(self):
        tree = ClockTree()
        tree.add_clock(Clock(name="a", rate=1e6, provider="cru"))
        tree.add_clock(Clock(name="b", rate=1e6, provider="cru", parent="a"))
        tree.clock_parents["a"] = "b"
        # just verify it doesn't raise
        tree.detect_cycles()


class TestRules:
    def test_pd001_missing_power(self):
        model = build_sample_model("rk3588")
        model.device_supplies["missing_device"] = ["nonexistent_power"]
        registry = RuleRegistry()
        register_rockchip_rules(registry, "rk3588")
        violations = Checker(registry).check(model, "rk3588")
        assert any(v.code == "PD-001" for v in violations)

    def test_ck101_cycle_detection(self):
        model = SoC(name="test")
        model.clock_tree.add_clock(Clock(name="clk_a", rate=1e6, provider="cru"))
        model.clock_tree.add_clock(Clock(name="clk_b", rate=1e6, provider="cru", parent="clk_a"))
        model.clock_tree.clock_parents["clk_a"] = "clk_b"
        registry = RuleRegistry()
        register_rockchip_rules(registry, "test")
        violations = Checker(registry).check(model, "test")
        assert any(v.code == "CK-101" for v in violations)

    def test_sample_model(self):
        model = build_sample_model("rk3588")
        assert "sys_pwr" in model.power_tree.nodes
        assert "i2c0" in model.devices


class TestChecker:
    def test_checker_generate_text_report(self):
        model = build_sample_model("rk3588")
        registry = RuleRegistry()
        register_rockchip_rules(registry, "rk3588")
        register_common_rules(registry)
        checker = Checker(registry)
        violations = checker.check(model, "rk3588")
        report = checker.generate_report(violations, output_format="text")
        assert isinstance(report, str) and len(report) > 0

    def test_checker_generate_json_report(self):
        import json
        model = build_sample_model("rk3588")
        registry = RuleRegistry()
        register_rockchip_rules(registry, "rk3588")
        checker = Checker(registry)
        violations = checker.check(model, "rk3588")
        report = checker.generate_report(violations, output_format="json")
        data = json.loads(report)
        assert "violations" in data and "summary" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DTS PARSER
# ═══════════════════════════════════════════════════════════════════════════════

from socc.parser.dts_parser import DTSTokenizer, DTSParser, parse_dts


class TestDTSTokenizer:
    def test_phandle(self):
        tokens = DTSTokenizer("&clk &gpio").tokenize()
        phandles = [t for t in tokens if t.type.name == "PHANDLE"]
        assert len(phandles) == 2
        assert phandles[0].value == "&clk"

    def test_escape_strings(self):
        tokens = DTSTokenizer(r'"hello\nworld"').tokenize()
        strings = [t for t in tokens if t.type.name == "STRING"]
        assert len(strings) == 1
        assert "\n" in strings[0].value

    def test_comments_stripped(self):
        content = "/* block */ val1 // line\nval2"
        tokens = DTSTokenizer(content).tokenize()
        node_names = [t for t in tokens if t.type.name == "NODE_NAME"]
        assert len(node_names) == 2

    def test_numbers(self):
        tokens = DTSTokenizer("123 0x456 0xABC").tokenize()
        numbers = [t for t in tokens if t.type.name == "NUMBER"]
        assert len(numbers) == 3


class TestDTSParser:
    def test_parse_simple(self):
        tree = parse_dts('/dts-v1/;\n/ {\n    model = "T";\n};\n')
        assert tree is not None and tree.get("type") == "root"

    def test_parse_nested_nodes(self):
        dts = '/dts-v1/;\n/ {\n    n1 {\n        n2 { p = "v"; };\n    };\n};\n'
        tree = parse_dts(dts)
        assert len(tree.get("children", [])) > 0

    def test_parse_with_labels(self):
        dts = '/dts-v1/;\n/ {\n    clk_root: clock { name = "root"; };\n};\n'
        assert parse_dts(dts) is not None

    def test_parse_array_properties(self):
        dts = '/dts-v1/;\n/ {\n    node {\n        clocks = <0x1 0x2 0x3>;\n    };\n};\n'
        assert parse_dts(dts) is not None


class TestDTSMapper:
    def test_map_with_regulators(self):
        from socc.parser.dts_mapper import dts_to_soc
        dts = '''/dts-v1/;
/ {
    regulators {
        vdd: dcdc { regulator-min-microvolt = <800000>; regulator-max-microvolt = <1100000>; };
    };
};
'''
        soc = dts_to_soc(parse_dts(dts), "test_soc")
        assert soc is not None and len(soc.power_tree.nodes) > 0

    def test_map_with_clocks(self):
        from socc.parser.dts_mapper import dts_to_soc
        dts = '/dts-v1/;\n/ {\n    clocks {\n        ref: clock { clock-frequency = <24000000>; };\n    };\n};\n'
        soc = dts_to_soc(parse_dts(dts), "test_soc")
        assert soc is not None


class TestDTSParseFile:
    def test_parse_file_not_found(self):
        from socc.parser import parse_dts_file
        with pytest.raises(FileNotFoundError):
            parse_dts_file("/nonexistent/file.dts")

    def test_parse_valid_dts_file(self, tmp_path):
        from socc.parser import parse_dts_file
        f = tmp_path / "t.dts"
        f.write_text('/dts-v1/;\n/ {\n    model = "T";\n};\n')
        soc = parse_dts_file(str(f), "test_soc")
        assert soc is not None and soc.name == "test_soc"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. MAINLINE DTS INTEGRATION (rk3588s / OrangePi 5)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def mainline_tree():
    text = MAINLINE_DTS.read_text()
    return parse_dts(text)


@pytest.fixture(scope="module")
def mainline_soc(mainline_tree):
    from socc.parser.dts_mapper import dts_to_soc
    return dts_to_soc(mainline_tree, "rk3588")


class TestMainlineDTSParsing:
    def test_fixture_exists(self):
        assert MAINLINE_DTS.exists(), f"Fixture missing: {MAINLINE_DTS}"

    def test_parse_produces_root_node(self, mainline_tree):
        assert mainline_tree is not None and mainline_tree.get("type") == "root"

    def test_root_has_children(self, mainline_tree):
        assert len(mainline_tree.get("children", [])) > 0

    def test_root_compatible_is_rk3588(self, mainline_tree):
        first_child = (mainline_tree.get("children") or [{}])[0]
        compat = first_child.get("properties", {}).get("compatible", "")
        if isinstance(compat, list):
            compat = " ".join(str(c) for c in compat)
        assert "rk3588" in compat.lower()

    def test_large_node_count(self, mainline_tree):
        def count(n):
            return 1 + sum(count(c) for c in n.get("children", []))
        assert count(mainline_tree) > 200


class TestMainlineSoCModel:
    def test_soc_name(self, mainline_soc):
        assert mainline_soc.name == "rk3588"

    def test_regulator_count(self, mainline_soc):
        assert len(mainline_soc.power_tree.nodes) >= 10

    def test_clock_providers_present(self, mainline_soc):
        assert len(mainline_soc.clock_tree.providers) >= 1

    def test_device_count(self, mainline_soc):
        assert len(mainline_soc.devices) >= 100

    def test_device_supplies_are_lists(self, mainline_soc):
        for dev, supplies in mainline_soc.device_supplies.items():
            assert isinstance(supplies, list), \
                f"device_supplies[{dev!r}] is {type(supplies).__name__}, not list"

    def test_no_regulator_cycles(self, mainline_soc):
        assert mainline_soc.power_tree.detect_cycles() == []

    def test_soc_validation_passes(self, mainline_soc):
        assert isinstance(mainline_soc.validate(), list)

    def test_known_regulator_present(self, mainline_soc):
        names = set(mainline_soc.power_tree.nodes)
        assert any("vcc5v0" in n for n in names), \
            f"vcc5v0_sys missing. Known: {sorted(names)[:10]}"

    def test_clock_controller_present(self, mainline_soc):
        providers = mainline_soc.clock_tree.providers
        assert any("fd7c0000" in k or "cru" in k.lower() for k in providers), \
            f"CRU not found. Providers: {list(providers.keys())}"


class TestMainlineRuleChecks:
    @pytest.fixture(scope="class")
    def violations(self, mainline_soc):
        from socc.engine.checker import Checker
        from socc.rules.registry import RuleRegistry
        from socc.rules.common import register_common_rules
        from socc.rules.rockchip import register_rockchip_rules
        registry = RuleRegistry()
        register_common_rules(registry)
        register_rockchip_rules(registry, "rk3588")
        return Checker(registry).check(mainline_soc, soc_name="rk3588")

    def test_check_returns_list(self, violations):
        assert isinstance(violations, list)

    def test_violations_have_rule_code(self, violations):
        for v in violations:
            assert hasattr(v, "code") and v.code

    def test_violations_have_message(self, violations):
        for v in violations:
            assert hasattr(v, "message") and v.message

    def test_zero_errors(self, violations):
        errors = [v for v in violations if getattr(v, "severity", "") == "error"]
        assert errors == [], \
            f"Expected 0 errors, got {len(errors)}: {[v.code for v in errors]}"

    def test_report_generation_text(self, violations):
        from socc.engine.checker import Checker
        from socc.rules.registry import RuleRegistry
        report = Checker(RuleRegistry()).generate_report(violations, output_format="text")
        assert isinstance(report, str) and len(report) > 0

    def test_report_generation_json(self, violations):
        import json
        from socc.engine.checker import Checker
        from socc.rules.registry import RuleRegistry
        report = Checker(RuleRegistry()).generate_report(violations, output_format="json")
        data = json.loads(report)
        assert "violations" in data and "summary" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 4. NXP VENDOR RULES  (IMX-001, IMX-002)
# ═══════════════════════════════════════════════════════════════════════════════

from socc.model.clock import ClockProvider
from socc.rules.nxp.imx_rules import (
    IMX001ArmPllFreqLimit,
    IMX002DramBeforeSocCore,
    register_nxp_rules,
)
from socc.rules.nxp import NXP_SOC_NAMES


def _nxp_clock_tree(clocks):
    ct = ClockTree()
    for name, rate_mhz, parent in clocks:
        try:
            ct.add_provider(ClockProvider(name=f"prov_{name}", type="pll"))
        except ValueError:
            pass
        ct.add_clock(Clock(name=name, rate=rate_mhz * 1_000_000,
                           provider=f"prov_{name}", parent=parent))
    return ct


def _nxp_power_tree(regs, edges=None):
    pt = PowerTree()
    for name, v_min, v_max, seq in regs:
        pt.add_regulator(Regulator(name=name, type="dcdc",
                                   voltage_min=v_min, voltage_max=v_max,
                                   sequence_order=seq))
    for parent, child in (edges or []):
        pt.add_edge(parent, child)
    return pt


def _nxp_soc(clock_tree=None, power_tree=None):
    return SoC(name="imx8mp", clock_tree=clock_tree or ClockTree(),
               power_tree=power_tree or PowerTree(), devices={})


def _imx_ctx():
    return CheckContext(soc_name="imx8mp")


class TestIMX001ArmPllFreqLimit:
    def test_arm_pll_within_limit_no_violation(self):
        model = _nxp_soc(clock_tree=_nxp_clock_tree([("arm_pll", 1800, None)]))
        assert IMX001ArmPllFreqLimit().check(model, _imx_ctx()) == []

    def test_arm_pll_below_limit_no_violation(self):
        model = _nxp_soc(clock_tree=_nxp_clock_tree([("arm_pll", 1600, None)]))
        assert IMX001ArmPllFreqLimit().check(model, _imx_ctx()) == []

    def test_arm_pll_over_limit_violation(self):
        model = _nxp_soc(clock_tree=_nxp_clock_tree([("arm_pll", 1900, None)]))
        v = IMX001ArmPllFreqLimit().check(model, _imx_ctx())
        assert len(v) == 1 and v[0].code == "IMX-001" and v[0].severity == "error"
        assert "1900" in v[0].message

    def test_arm_a53_clk_over_limit(self):
        model = _nxp_soc(clock_tree=_nxp_clock_tree([("arm_a53_clk", 2000, None)]))
        v = IMX001ArmPllFreqLimit().check(model, _imx_ctx())
        assert len(v) == 1 and v[0].code == "IMX-001"

    def test_other_pll_over_limit_no_violation(self):
        model = _nxp_soc(clock_tree=_nxp_clock_tree([("sys_pll1", 2000, None)]))
        assert IMX001ArmPllFreqLimit().check(model, _imx_ctx()) == []

    def test_no_clocks_no_violation(self):
        assert IMX001ArmPllFreqLimit().check(_nxp_soc(), _imx_ctx()) == []

    def test_multiple_arm_clocks_one_over(self):
        ct = _nxp_clock_tree([("arm_pll", 1800, None), ("arm_a53_clk", 1850, "arm_pll")])
        model = _nxp_soc(clock_tree=ct)
        v = IMX001ArmPllFreqLimit().check(model, _imx_ctx())
        assert len(v) == 1 and "arm_a53_clk" in v[0].message


class TestIMX002DramBeforeSocCore:
    def test_correct_sequence_no_violation(self):
        pt = _nxp_power_tree([("nvcc_dram", 1.05, 1.10, 1), ("vdd_soc", 0.80, 0.90, 2)])
        assert IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx()) == []

    def test_inverted_sequence_violation(self):
        pt = _nxp_power_tree([("nvcc_dram", 1.05, 1.10, 3), ("vdd_soc", 0.80, 0.90, 2)])
        v = IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx())
        assert len(v) == 1 and v[0].code == "IMX-002" and v[0].severity == "error"
        assert "nvcc_dram" in v[0].message.lower()

    def test_same_order_violation(self):
        pt = _nxp_power_tree([("nvcc_dram", 1.05, 1.10, 2), ("vdd_soc", 0.80, 0.90, 2)])
        v = IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx())
        assert len(v) == 1

    def test_missing_dram_supply_no_violation(self):
        pt = _nxp_power_tree([("vdd_soc", 0.80, 0.90, 1)])
        assert IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx()) == []

    def test_missing_core_supply_no_violation(self):
        pt = _nxp_power_tree([("nvcc_dram", 1.05, 1.10, 1)])
        assert IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx()) == []

    def test_buck3_buck1_naming_detected(self):
        pt = _nxp_power_tree([("buck3", 1.05, 1.10, 5), ("buck1", 0.80, 0.90, 2)])
        v = IMX002DramBeforeSocCore().check(_nxp_soc(power_tree=pt), _imx_ctx())
        assert len(v) == 1 and "buck3" in v[0].message.lower()


class TestNXPRegistration:
    def test_register_nxp_rules(self):
        registry = RuleRegistry()
        register_nxp_rules(registry, "imx8mp")
        codes = {r.code for r in registry.get_rules_for_soc("imx8mp")}
        assert "IMX-001" in codes and "IMX-002" in codes

    def test_nxp_soc_names_list(self):
        assert "imx8mp" in NXP_SOC_NAMES

    def test_duplicate_registration_raises(self):
        registry = RuleRegistry()
        register_nxp_rules(registry, "imx8mp")
        with pytest.raises(ValueError):
            register_nxp_rules(registry, "imx8mp")


class TestIMX8MPYaml:
    _yaml_path = Path(__file__).parent.parent / "data" / "soc" / "nxp" / "imx8mp.yaml"

    def test_yaml_exists(self):
        assert self._yaml_path.exists(), f"Missing: {self._yaml_path}"

    def test_yaml_loadable(self):
        import yaml
        data = yaml.safe_load(self._yaml_path.read_text())
        assert data["soc"] == "imx8mp"

    def test_yaml_has_arm_pll(self):
        import yaml
        data = yaml.safe_load(self._yaml_path.read_text())
        pll_names = [p["name"] for p in data["clocks"]["plls"]]
        assert "arm_pll" in pll_names

    def test_yaml_has_pmic_outputs(self):
        import yaml
        data = yaml.safe_load(self._yaml_path.read_text())
        names = [o["name"] for o in data["power_tree_constraints"]["pmic_outputs"]]
        assert "BUCK1" in names and "BUCK3" in names


# ═══════════════════════════════════════════════════════════════════════════════
# 5. COMMON RULES  (PIN-301, COMP-101, PWR-101, PWR-102)
# ═══════════════════════════════════════════════════════════════════════════════

from socc.rules.common.pin_rules import PIN301PhantomPinmux
from socc.rules.common.compat_rules import COMP101DeprecatedVendorBinding
from socc.rules.common.power_audit_rules import (
    PWR101AlwaysOnPeripheralRail,
    PWR102PMICMissingWakeupSource,
)


class TestPIN301PhantomPinmux:
    rule = PIN301PhantomPinmux()

    def test_i2c_with_pinctrl_ok(self):
        node = _irnode("i2c2", props={"status": "okay", "compatible": "rockchip,rk3588-i2c",
                                      "pinctrl-0": ["&i2c2m0_xfer"], "pinctrl-names": "default"})
        assert self.rule.check(_make_soc_v07(devices={"i2c2": node}), _ctx()) == []

    def test_disabled_uart_ok(self):
        node = _irnode("uart4", props={"status": "disabled", "compatible": "snps,dw-apb-uart"})
        assert self.rule.check(_make_soc_v07(devices={"uart4": node}), _ctx()) == []

    def test_non_bus_device_ignored(self):
        node = _irnode("leds", props={"status": "okay", "compatible": "gpio-leds"})
        assert self.rule.check(_make_soc_v07(devices={"leds": node}), _ctx()) == []

    def test_i2c_no_pinctrl_fires(self):
        node = _irnode("i2c5", props={"status": "okay", "compatible": "rockchip,rk3588-i2c"})
        v = self.rule.check(_make_soc_v07(devices={"i2c5": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PIN-301" and v[0].severity == "error"

    def test_uart_no_pinctrl_fires(self):
        node = _irnode("serial3", props={"status": "okay", "compatible": "snps,dw-apb-uart"})
        v = self.rule.check(_make_soc_v07(devices={"serial3": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PIN-301"

    def test_spi_no_pinctrl_fires(self):
        node = _irnode("spi0", props={"status": "okay", "compatible": "rockchip,rk3588-spi"})
        v = self.rule.check(_make_soc_v07(devices={"spi0": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PIN-301"

    def test_multiple_violations(self):
        soc = _make_soc_v07(devices={
            "i2c3":  _irnode("i2c3",  props={"status": "okay", "compatible": "rockchip,rk3588-i2c"}),
            "uart0": _irnode("uart0", props={"status": "okay", "compatible": "snps,dw-apb-uart"}),
        })
        v = self.rule.check(soc, _ctx())
        assert len(v) == 2 and {x.code for x in v} == {"PIN-301"}

    def test_suggestion_contains_pinctrl_snippet(self):
        node = _irnode("i2c1", props={"status": "okay", "compatible": "rockchip,rk3588-i2c"})
        v = self.rule.check(_make_soc_v07(devices={"i2c1": node}), _ctx())
        assert "pinctrl-names" in v[0].suggestion and "pinctrl-0" in v[0].suggestion


class TestCOMP101DeprecatedVendorBinding:
    rule = COMP101DeprecatedVendorBinding()

    def test_mainline_binding_ok(self):
        node = _irnode("vop", props={"compatible": "rockchip,rk3588-vop"})
        assert self.rule.check(_make_soc_v07(devices={"vop": node}), _ctx()) == []

    def test_deprecated_single_compat_fires(self):
        node = _irnode("vop_core", props={"compatible": "rockchip,rk3588-vop-core"})
        v = self.rule.check(_make_soc_v07(devices={"vop_core": node}), _ctx())
        assert len(v) == 1 and v[0].code == "COMP-101"
        assert "rockchip,rk3588-vop-core" in v[0].message
        assert "rockchip,rk3588-vop" in v[0].suggestion

    def test_deprecated_in_list_compat_fires(self):
        node = _irnode("crypto", props={"compatible": ["rockchip,rk3588s-crypto", "rockchip,crypto"]})
        v = self.rule.check(_make_soc_v07(devices={"crypto": node}), _ctx())
        assert len(v) == 1 and v[0].code == "COMP-101"

    def test_nxp_deprecated_binding_fires(self):
        node = _irnode("blk_ctrl", props={"compatible": "fsl,imx8mm-blk-ctrl"})
        v = self.rule.check(_make_soc_v07(devices={"blk_ctrl": node}), _ctx())
        assert len(v) == 1 and "fsl,imx8mm-media-blk-ctrl" in v[0].suggestion


class TestPWR101AlwaysOnPeripheralRail:
    rule = PWR101AlwaysOnPeripheralRail()

    def test_no_always_on_ok(self):
        node = _irnode("vcc_3v3", props={"compatible": "regulator-fixed",
                                          "regulator-min-microvolt": 3300000})
        assert self.rule.check(_make_soc_v07(devices={"vcc_3v3": node}), _ctx()) == []

    def test_always_on_critical_supply_ok(self):
        node = _irnode("vdd_cpu", props={"compatible": "regulator-fixed",
                                          "regulator-always-on": True})
        assert self.rule.check(_make_soc_v07(devices={"vdd_cpu": node}), _ctx()) == []

    def test_always_on_peripheral_no_consumers_info(self):
        node = _irnode("vcc_wifi", props={"compatible": "regulator-fixed",
                                           "regulator-always-on": True})
        v = self.rule.check(_make_soc_v07(devices={"vcc_wifi": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PWR-101" and v[0].severity == "info"

    def test_always_on_peripheral_with_consumer_warning(self):
        node = _irnode("vcc_usb", props={"compatible": "regulator-fixed",
                                          "regulator-always-on": True})
        soc = _make_soc_v07(devices={"vcc_usb": node},
                             device_supplies={"usb_hub": ["vcc_usb"]})
        v = self.rule.check(soc, _ctx())
        assert len(v) == 1 and v[0].code == "PWR-101" and v[0].severity == "warning"


class TestPWR102PMICMissingWakeupSource:
    rule = PWR102PMICMissingWakeupSource()

    def test_non_pmic_device_ok(self):
        node = _irnode("tmp102", props={"compatible": "ti,tmp102", "status": "okay"})
        assert self.rule.check(_make_soc_v07(devices={"tmp102": node}), _ctx()) == []

    def test_pmic_with_wakeup_ok(self):
        node = _irnode("pmic", props={"compatible": "rockchip,rk809", "status": "okay",
                                       "wakeup-source": True})
        assert self.rule.check(_make_soc_v07(devices={"pmic": node}), _ctx()) == []

    def test_pmic_missing_wakeup_fires(self):
        node = _irnode("pmic", props={"compatible": "rockchip,rk817", "status": "okay"})
        v = self.rule.check(_make_soc_v07(devices={"pmic": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PWR-102" and v[0].severity == "warning"
        assert "wakeup-source" in v[0].suggestion

    def test_pmic_disabled_ok(self):
        node = _irnode("pmic", props={"compatible": "rockchip,rk808", "status": "disabled"})
        assert self.rule.check(_make_soc_v07(devices={"pmic": node}), _ctx()) == []

    def test_axp_pmic_fires(self):
        node = _irnode("axp803", props={"compatible": "x-powers,axp803", "status": "okay"})
        v = self.rule.check(_make_soc_v07(devices={"axp803": node}), _ctx())
        assert len(v) == 1 and v[0].code == "PWR-102"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. IRQ COLLISION & BOUNDS AUDITOR
# ═══════════════════════════════════════════════════════════════════════════════


def _make_clean_soc() -> SoC:
    pt, ct = PowerTree(), ClockTree()
    devices = {
        "i2c0": _irnode("i2c0", "/soc/i2c@fe2b0000",
                         {"reg": [0xfe2b0000, 0x1000], "status": "okay",
                          "compatible": "snps,dw-apb-i2c"}),
        "spi0": _irnode("spi0", "/soc/spi@fe2c0000",
                         {"reg": [0xfe2c0000, 0x1000], "status": "okay",
                          "compatible": "rockchip,sfc"}),
    }
    return SoC("rk3588", pt, ct, devices, {}, {}, {}, {})


class TestIRQChecker:
    def test_clean_soc_no_issues(self):
        from socc.irqcheck import check_irq
        report = check_irq(_make_clean_soc())
        assert report.pass_result and report.critical_count == 0

    def test_unique_irqs_pass(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "spi0":  _irnode("spi0",  "/soc/spi",  {"status": "okay", "interrupts": [0, 45, 4]}),
            "uart0": _irnode("uart0", "/soc/uart", {"status": "okay", "interrupts": [0, 46, 4]}),
        }
        report = check_irq(SoC("rk3588", pt, ct, devices, {}, {}, {}, {}))
        assert report.pass_result

    def test_irq_collision_detected(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "spi0":  _irnode("spi0",  "/soc/spi",  {"status": "okay", "interrupts": [0, 45, 4]}),
            "uart0": _irnode("uart0", "/soc/uart", {"status": "okay", "interrupts": [0, 45, 4]}),
        }
        report = check_irq(SoC("rk3588", pt, ct, devices, {}, {}, {}, {}))
        assert not report.pass_result
        assert report.critical_count >= 1
        assert any(i.rule_id == "IRQ-C01" for i in report.issues)

    def test_collision_lists_both_nodes(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "devA": _irnode("devA", "/soc/a", {"status": "okay", "interrupts": [0, 10, 4]}),
            "devB": _irnode("devB", "/soc/b", {"status": "okay", "interrupts": [0, 10, 4]}),
        }
        report = check_irq(SoC("t", pt, ct, devices, {}, {}, {}, {}))
        fatal = [i for i in report.issues if i.rule_id == "IRQ-C01"]
        assert len(fatal) >= 1
        names = [n for n, _ in fatal[0].nodes]
        assert "devA" in names and "devB" in names

    def test_disabled_node_not_in_collision(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "active": _irnode("active", "/soc/a", {"status": "okay",    "interrupts": [0, 45, 4]}),
            "off":    _irnode("off",    "/soc/b", {"status": "disabled", "interrupts": [0, 45, 4]}),
        }
        report = check_irq(SoC("t", pt, ct, devices, {}, {}, {}, {}))
        assert report.pass_result

    def test_reserved_ppi_flagged(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {"bad": _irnode("bad", "/soc/bad", {"status": "okay", "interrupts": [1, 2, 4]})}
        report = check_irq(SoC("t", pt, ct, devices, {}, {}, {}, {}))
        assert any(i.rule_id == "IRQ-C02" for i in report.issues)

    def test_valid_ppi_not_flagged(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {"wdt": _irnode("wdt", "/soc/wdt", {"status": "okay", "interrupts": [1, 9, 4]})}
        report = check_irq(SoC("t", pt, ct, devices, {}, {}, {}, {}))
        assert not any(i.rule_id == "IRQ-C02" for i in report.issues)

    def test_interrupt_parent_missing_flagged(self):
        from socc.irqcheck import check_irq
        pt, ct = PowerTree(), ClockTree()
        devices = {"dev": _irnode("dev", "/soc/dev",
                                   {"status": "okay", "interrupt-parent": "&ghost_gic",
                                    "interrupts": [0, 5, 4]})}
        report = check_irq(SoC("t", pt, ct, devices, {}, {}, {}, {}))
        assert any(i.rule_id == "IRQ-C03" for i in report.issues)


class TestBoundsAuditor:
    def test_gpio_pin_out_of_range_fatal(self):
        from socc.bounds import check_bounds
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "led": _irnode("led", "/leds/led0",
                            {"status": "okay", "gpios": ["&gpio4", 35, 0]}),  # 35 > 31
        }
        report = check_bounds(SoC("rk3588", pt, ct, devices, {}, {}, {}, {}))
        assert not report.pass_result
        assert any(i.rule_id == "BND-001" for i in report.issues)

    def test_dma_channel_out_of_range_fatal(self):
        from socc.bounds import check_bounds
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "spi": _irnode("spi", "/soc/spi",
                            {"status": "okay", "dmas": ["&dmac0", 48]}),  # 48 >= 32
        }
        report = check_bounds(SoC("rk3588", pt, ct, devices, {}, {}, {}, {}))
        assert any(i.rule_id == "BND-002" for i in report.issues)

    def test_disabled_node_not_checked(self):
        from socc.bounds import check_bounds
        pt, ct = PowerTree(), ClockTree()
        devices = {
            "led": _irnode("led", "/leds/led0",
                            {"status": "disabled", "gpios": ["&gpio4", 99, 0]}),
        }
        report = check_bounds(SoC("rk3588", pt, ct, devices, {}, {}, {}, {}))
        assert report.pass_result


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SIMULATION  (PS-001/002/003, CG-001/002, RS-001/002)
# ═══════════════════════════════════════════════════════════════════════════════

from socc.simulation.types import (
    RegulatorState, ClockState, DeviceState, SimViolation,
)


class TestPowerStateMachine:
    def test_boot_all_regulators_on(self):
        from socc.simulation.power_sim import PowerStateMachine
        pt = _simple_power_tree()
        sm = PowerStateMachine(pt)
        sm.simulate_boot({})
        for name in pt.nodes:
            assert sm.states[name] == RegulatorState.ON

    def test_boot_no_violations_on_clean_tree(self):
        from socc.simulation.power_sim import PowerStateMachine
        _, violations = PowerStateMachine(_simple_power_tree()).simulate_boot({})
        assert violations == []

    def test_ps001_triggered_when_ramp_below_requirement(self):
        from socc.simulation.power_sim import PowerStateMachine
        pt = _simple_power_tree()
        sm = PowerStateMachine(pt, stability_requirements={"vcc_3v3": 5.0})
        _, violations = sm.simulate_boot({"i2c0": ["vcc_3v3"]})
        ps001 = [v for v in violations if v.code == "PS-001"]
        assert len(ps001) >= 1 and ps001[0].severity == "warning"
        assert "vcc_3v3" in ps001[0].component

    def test_ps001_not_triggered_when_ramp_meets_requirement(self):
        from socc.simulation.power_sim import PowerStateMachine
        pt = PowerTree()
        pt.nodes["vcc_3v3"] = _reg("vcc_3v3", startup_us=10_000)
        sm = PowerStateMachine(pt, stability_requirements={"vcc_3v3": 5.0})
        _, violations = sm.simulate_boot({"i2c0": ["vcc_3v3"]})
        assert not any(v.code == "PS-001" for v in violations)

    def test_ps003_full_simulation_no_false_positive(self):
        from socc.simulation.power_sim import PowerStateMachine
        _, violations = PowerStateMachine(_simple_power_tree()).simulate_boot({})
        assert not any(v.code == "PS-003" for v in violations)

    def test_ps002_triggered_when_supply_off_before_suspend(self):
        from socc.simulation.power_sim import PowerStateMachine
        sm = PowerStateMachine(_simple_power_tree())
        _, violations = sm.simulate_suspend(
            device_supplies={"i2c0": ["vcc_3v3"]},
            device_probe_order=[],  # i2c0 never suspended
        )
        ps002 = [v for v in violations if v.code == "PS-002"]
        assert len(ps002) >= 1 and "vcc_3v3" in ps002[0].component

    def test_ps002_not_triggered_when_all_suspended(self):
        from socc.simulation.power_sim import PowerStateMachine
        sm = PowerStateMachine(_simple_power_tree())
        _, violations = sm.simulate_suspend(
            device_supplies={"i2c0": ["vcc_3v3"]},
            device_probe_order=["i2c0"],
        )
        assert not any(v.code == "PS-002" for v in violations)


class TestClockStateMachine:
    def test_enable_all_clocks_become_enabled(self):
        from socc.simulation.clock_sim import ClockStateMachine
        ct = _simple_clock_tree()
        sm = ClockStateMachine(ct)
        sm.simulate_enable()
        for name in ct.clocks:
            assert sm.states[name] == ClockState.ENABLED

    def test_cg001_triggered_when_active_consumer_clock_gated(self):
        from socc.simulation.clock_sim import ClockStateMachine
        ct = _simple_clock_tree()
        sm = ClockStateMachine(ct)
        sm.reset_to_enabled()
        _, violations = sm.simulate_power_off_impact(
            disabled_regulator="vcc_3v3",
            device_supplies={"i2c0": ["vcc_3v3"]},
            device_clocks={"i2c0": ["clk_i2c0"]},
            device_states={"i2c0": DeviceState.ACTIVE},
        )
        cg001 = [v for v in violations if v.code == "CG-001"]
        assert len(cg001) >= 1 and "clk_i2c0" in cg001[0].component

    def test_cg001_not_triggered_when_suspended(self):
        from socc.simulation.clock_sim import ClockStateMachine
        ct = _simple_clock_tree()
        sm = ClockStateMachine(ct)
        sm.reset_to_enabled()
        _, violations = sm.simulate_power_off_impact(
            disabled_regulator="vcc_3v3",
            device_supplies={"i2c0": ["vcc_3v3"]},
            device_clocks={"i2c0": ["clk_i2c0"]},
            device_states={"i2c0": DeviceState.SUSPENDED},
        )
        assert not any(v.code == "CG-001" for v in violations)

    def test_cg002_triggered_when_parent_disabled(self):
        from socc.simulation.clock_sim import ClockStateMachine
        ct = ClockTree()
        ct.clocks["pll_gpll"]  = _clk("pll_gpll")
        ct.clocks["clk_uart0"] = _clk("clk_uart0", parent="pll_gpll", consumers=["uart0"])
        sm = ClockStateMachine(ct)
        sm.reset_to_enabled()
        sm.states["pll_gpll"] = ClockState.DISABLED
        violations = sm.check_parent_clock_violations(
            device_clocks={"uart0": ["clk_uart0"]},
            device_states={"uart0": DeviceState.ACTIVE},
        )
        cg002 = [v for v in violations if v.code == "CG-002"]
        assert len(cg002) >= 1 and "pll_gpll" in cg002[0].component


class TestResetStateMachine:
    def test_rs001_triggered_when_provider_not_ready(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(reset_dependencies=[
            {"device_pattern": "i2c@", "requires_before_deassert": ["cru"]}
        ])
        _, violations = sm.simulate_boot_deassert(
            devices={"i2c0": _irnode("i2c0", "/soc/i2c@fe2b0000")},
            provider_ready=[],
        )
        rs001 = [v for v in violations if v.code == "RS-001"]
        assert len(rs001) == 1 and rs001[0].severity == "error"

    def test_rs001_not_triggered_when_provider_ready(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(reset_dependencies=[
            {"device_pattern": "i2c@", "requires_before_deassert": ["cru"]}
        ])
        _, violations = sm.simulate_boot_deassert(
            devices={"i2c0": _irnode("i2c0", "/soc/i2c@fe2b0000")},
            provider_ready=["cru"],
        )
        assert not any(v.code == "RS-001" for v in violations)

    def test_rs001_case_insensitive(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(reset_dependencies=[
            {"device_pattern": "uart@", "requires_before_deassert": ["CRU"]}
        ])
        _, violations = sm.simulate_boot_deassert(
            devices={"uart0": _irnode("uart0", "/soc/uart@feb50000")},
            provider_ready=["cru"],
        )
        assert not any(v.code == "RS-001" for v in violations)

    def test_rs002_triggered_on_missing_resets(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(required_resets_patterns=[
            {"device_pattern": "spi@", "required": True}
        ])
        violations = sm.check_missing_resets(
            {"spi0": _irnode("spi0", "/soc/spi@fec10000", {})}
        )
        rs002 = [v for v in violations if v.code == "RS-002"]
        assert len(rs002) == 1 and rs002[0].severity == "warning"

    def test_rs002_not_triggered_when_resets_present(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(required_resets_patterns=[
            {"device_pattern": "spi@", "required": True}
        ])
        violations = sm.check_missing_resets(
            {"spi0": _irnode("spi0", "/soc/spi@fec10000", {"resets": [1, 5]})}
        )
        assert not violations

    def test_rs002_only_matches_specified_pattern(self):
        from socc.simulation.reset_sim import ResetStateMachine
        sm = ResetStateMachine(required_resets_patterns=[
            {"device_pattern": "spi@", "required": True}
        ])
        violations = sm.check_missing_resets(
            {"i2c0": _irnode("i2c0", "/soc/i2c@fe2b0000", {})}
        )
        assert not violations


# ═══════════════════════════════════════════════════════════════════════════════
# 8. REGRESSION: FALSE POSITIVE FIXES
#    (DMA-001 tightened, CK-107 suggestion, PD-006 power-domain@N, PD-007 sev)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_simple_soc(devices: dict) -> SoC:
    devs = {k: _irnode(k, props=v) for k, v in devices.items()}
    return SoC(name="rk3588", devices=devs,
               power_tree=PowerTree(), clock_tree=ClockTree())


class TestDMA001FalsePositives:
    """Tightened DMA-master detection: false positives must not fire."""

    def _run(self, devices: dict):
        from socc.rules.common.iommu_rules import DMA001MissingIommuBinding
        return DMA001MissingIommuBinding().check(
            _make_simple_soc(devices), CheckContext(soc_name="rk3588")
        )

    def test_grf_syscon_not_dma_master(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "syscon@fd5a4000": {"compatible": "rockchip,rk3588-vop-grf, syscon"},
        })
        assert not any("fd5a4000" in (x.location or "") for x in v if x.code == "DMA-001"), \
            "vop-grf syscon must NOT be flagged"

    def test_i2s_dma_engine_client_excluded(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "i2s@fddc0000": {"compatible": "rockchip,rk3588-i2s-tdm",
                              "dmas": "<&dmac0 0 &dmac0 1>"},
        })
        assert not any("i2s" in (x.location or "") for x in v if x.code == "DMA-001"), \
            "I2S with dmas must NOT be flagged"

    def test_spdif_dma_engine_client_excluded(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "spdif-tx@fddb0000": {"compatible": "rockchip,rk3588-spdif",
                                   "dmas": "<&dmac0 2>"},
        })
        assert not any("spdif" in (x.location or "") for x in v if x.code == "DMA-001"), \
            "SPDIF with dmas must NOT be flagged"

    def test_pl330_dma_controller_excluded(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "dma-controller@fea10000": {"compatible": "arm,pl330"},
        })
        assert not any("pl330" in (x.location or "") or "fea1" in (x.location or "")
                       for x in v if x.code == "DMA-001"), \
            "arm,pl330 controller must NOT be flagged"

    def test_hdmi_connector_excluded(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "hdmi0-con": {"compatible": "hdmi-connector"},
        })
        assert not any("hdmi0-con" in (x.location or "") for x in v if x.code == "DMA-001"), \
            "hdmi-connector must NOT be flagged"

    def test_real_dma_master_still_fires(self):
        v = self._run({
            "iommu0": {"compatible": "rockchip,iommu"},
            "gpu@fb000000": {"compatible": "rockchip,rk3588-mali"},
        })
        assert any(x.code == "DMA-001" for x in v), \
            "GPU without iommus must still fire DMA-001"


class TestCK107:
    """CK-107: token safety and no fabricated patch suggestions."""

    def _run(self, devices: dict):
        from socc.rules.rockchip.clock_rules import CK107AssignedClockRatesMissing
        return CK107AssignedClockRatesMissing().check(
            _make_simple_soc(devices), CheckContext(soc_name="rk3588")
        )

    def test_usb3_token_does_not_match_fusb302(self):
        v = self._run({"fusb302@22": {"compatible": "fcs,fusb302"}})
        assert not any(x.code == "CK-107" for x in v), \
            "fusb302 must NOT trigger CK-107"

    def test_dp_token_does_not_match_usbdpphy(self):
        v = self._run({
            "usbdpphy0": {"compatible": "rockchip,rk3588-usbdp-phy"},
            "hdptx@feda0000": {"compatible": "rockchip,rk3588-hdptx"},
        })
        assert not any(x.code == "CK-107" for x in v), \
            "usbdp-phy / hdptx must NOT trigger CK-107"

    def test_phy_nodes_excluded(self):
        v = self._run({"usb2phy@0": {"compatible": "rockchip,rk3588-usb2phy"}})
        assert not any(x.code == "CK-107" for x in v), "*-phy must be excluded"

    def test_suggestion_no_fabricated_clk_id(self):
        v = self._run({"pcie@fe180000": {"compatible": "rockchip,rk3588-pcie"}})
        for x in v:
            if x.code == "CK-107":
                assert "CLK_USB3" not in (x.suggestion or ""), \
                    "Must not contain hardcoded CLK_USB3"
                assert "500000000" not in (x.suggestion or ""), \
                    "Must not contain hardcoded 500000000"

    def test_suggestion_says_manual_review(self):
        v = self._run({"hdmi@fde80000": {"compatible": "rockchip,rk3588-dw-hdmi-qp"}})
        ck107 = [x for x in v if x.code == "CK-107"]
        assert ck107, "hdmi without assigned-clock-rates must fire CK-107"
        for x in ck107:
            s = (x.suggestion or "").lower()
            assert "trm" in s or "upstream" in s or "manual" in s, \
                "Suggestion must mention TRM / upstream / manual review"


class TestCK106ProviderSpecFormat:
    def test_cru_provider_spec_not_flagged(self):
        from socc.rules.rockchip.clock_rules import CK106ClockSourceContention
        soc = SoC(name="rk3588", devices={},
                  power_tree=PowerTree(), clock_tree=ClockTree(),
                  device_clocks={
                      "uart0": ["cru:0x00000119"],
                      "uart1": ["cru:0x00000119"],
                      "uart2": ["cru:0x00000119"],
                  })
        v = CK106ClockSourceContention().check(soc, CheckContext(soc_name="rk3588"))
        assert not any(x.code == "CK-106" for x in v), \
            "'provider:spec' clocks must not be flagged as contention"


class TestPD006PowerDomainNodes:
    def test_power_domain_subnode_not_registered_as_regulator(self):
        """power-domain@N with #power-domain-cells=0 must NOT be a provider."""
        from socc.parser.dts_parser import parse_dts
        from socc.parser.dts_mapper import DTSMapper

        dts_text = """/dts-v1/;
/ {
    #address-cells = <0x02>;
    #size-cells = <0x02>;
    power-controller@fd8d8000 {
        compatible = "rockchip,rk3588-power-controller";
        reg = <0x00 0xfd8d8000 0x00 0x1000>;
        #power-domain-cells = <0x01>;
        power-domain@0 { reg = <0x00>; #power-domain-cells = <0x00>; };
        power-domain@1 { reg = <0x01>; #power-domain-cells = <0x00>; };
    };
};
"""
        model = DTSMapper(parse_dts(dts_text), soc_name="rk3588").map()
        provider_names = [r.name for r in getattr(model.power_tree, "regulators", [])]
        for pd in ("power-domain@0", "power-domain@1"):
            assert pd not in provider_names, \
                f"'{pd}' (#power-domain-cells=0) must NOT be a provider"


class TestPD007Severity:
    def test_pd007_io_before_core_is_warning(self):
        from socc.rules.common.power_rules import PD007IOBeforeCoreSequence
        assert PD007IOBeforeCoreSequence().severity == "warning", \
            "PD-007 (IOBeforeCore) must have severity='warning'"
