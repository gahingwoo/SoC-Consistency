# Development Guide

## Requirements

- Python 3.8 or later
- pip

Optional (for regenerating the mainline DTS fixture):
- `clang` (for C preprocessor expansion of DTS source)
- `dtc` (Device Tree Compiler, for compiling/decompiling to resolve expressions)

---

## Setup

```bash
git clone https://github.com/gahingwoo/SoC-Consistency.git
cd SoC-Consistency
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verify the install:

```bash
socc --version
python -m pytest tests/ -q
```

---

## Running Tests

```bash
# All tests
python -m pytest tests/

# Verbose output
python -m pytest tests/ -v

# Stop on first failure
python -m pytest tests/ -x
```

All tests must pass before submitting a change.

---

## Project Layout

```
socc/               Source package
  cli.py            Click CLI — 43 commands
  __main__.py       Entry point for python -m socc
  parser/
    dts_parser.py   Tokenizer + recursive-descent parser
    dts_mapper.py   Maps parsed tree to SoC IR model
  model/
    base.py         IRNode, Violation, Device dataclasses
    clock.py        Clock, ClockProvider, ClockTree
    power.py        Regulator, PowerTree
    soc.py          SoC root dataclass
    thermal.py      ThermalZone, TripPoint
  rules/
    base.py         BaseRule ABC, CheckContext
    registry.py     RuleRegistry
    common/         Cross-SoC rules (BW, PIN, SEC, THM, NET, ...)
    rockchip/       Rockchip-specific rules
    allwinner/      Allwinner-specific rules
    amlogic/        Amlogic-specific rules
    qualcomm/       Qualcomm-specific rules
    nxp/            NXP i.MX-specific rules
  engine/
    checker.py      Rule executor + violation aggregator
  memmap.py         MMIO sweep-line overlap scanner (MM-xxx)
  depgraph.py       Power/clock DFS cycle detector (DG-xxx)
  smartdiff.py      Semantic DTS/DTB differ
  gc.py             Zombie-node garbage collector
  bounds.py         Physical resource bounds auditor (BND-xxx)
  irqcheck.py       IRQ collision checker (IRQ-Cxx)
  autofix.py        DTS patch generator
  report.py         HTML report renderer
  compliance.py     Kernel compliance checker
  kernel_audit.py   Kernel driver audit
meta-socc/          Yocto meta-layer for running socc in a BSP build
data/
  examples/         Demo DTS fixtures (contain intentional violations)
  soc/              YAML hardware-constraint files per SoC
docs/               Documentation
scripts/            Helper scripts
```

---

## Adding a New Rule

### 1. Choose the correct module

Rules live in `socc/rules/<vendor>/` or `socc/rules/common/` for SoC-agnostic
checks.

### 2. Implement BaseRule

```python
from socc.rules.base import BaseRule, CheckContext
from socc.model import SoC, Violation

class PD007MyNewRule(BaseRule):
    rule_id  = "PD-007"
    name     = "My new power rule"
    severity = "error"
    soc_types = ["rk3588"]

    def check(self, model: SoC, context: CheckContext) -> list[Violation]:
        violations = []
        for name, reg in model.power_tree.nodes.items():
            if some_condition(reg):
                violations.append(Violation(
                    code       = self.rule_id,
                    severity   = self.severity,
                    message    = f"Regulator {name} has a problem",
                    impact     = "Device will not power up",
                    suggestion = "Fix the regulator configuration",
                    location   = f"/regulators/{name}",
                ))
        return violations
```

### 3. Register the rule

In `socc/rules/rockchip/__init__.py`:

```python
from .power_rules import PD007MyNewRule

def register_rockchip_rules(registry, soc_name):
    ...
    registry.register(PD007MyNewRule(), soc_name)
```

### 4. Write a test

```python
def test_pd007_my_new_rule():
    from socc.rules.rockchip.power_rules import PD007MyNewRule
    from socc.model import SoC
    from socc.rules.base import CheckContext

    rule = PD007MyNewRule()
    ctx  = CheckContext(soc_name="rk3588")

    # Case: no violation
    model = SoC(name="rk3588")
    assert rule.check(model, ctx) == []

    # Case: violation triggered
    model2 = SoC(name="rk3588")
    # ... configure model2 to trigger the rule ...
    violations = rule.check(model2, ctx)
    assert len(violations) == 1
    assert violations[0].code == "PD-007"
```

---

## Adding a New SoC

1. Create `socc/rules/<vendor>/` with the following files:
   - `__init__.py` exposing `register_<vendor>_rules(registry, soc_name)`
   - Individual rule modules (e.g. `power_rules.py`, `clock_rules.py`)

2. Add a YAML constraint file at `data/soc/<vendor>/<soc>.yaml`:

```yaml
name: my-soc
vendor: myvendor
regulators:
  vdd_core:
    type: dcdc
    min_voltage_mv: 700
    max_voltage_mv: 1100
clocks:
  pll_core:
    min_freq_hz: 100000000
    max_freq_hz: 2000000000
```

3. Register the new SoC in `socc/cli.py` inside `_build_registry()`.

---

## Coding Conventions

- Follow PEP 8.
- Rule classes must be named with their rule code prefix, e.g. `PD001...`,
  `CK102...`.
- Rule IDs use the format `XX-NNN` where `XX` is the subsystem prefix and
  `NNN` is a three-digit number.
- `severity` must be one of `"error"`, `"warning"`, or `"info"`.
- Every `Violation` must populate `code`, `severity`, `message`, `impact`,
  `suggestion`, and `location`.
- Tests live in `tests/` and are named `test_<area>.py`.
- Each test function exercises exactly one scenario (pass or fail).

---

## Regenerating the Mainline DTS Fixture

The file `data/examples/rk3588s-orangepi-5.dts` is a preprocessed,
expression-resolved copy of the OrangePi 5 DTS from the mainline Linux kernel.

To regenerate it:

```
# 1. Download source files from torvalds/linux master
#    (rk3588s-orangepi-5.dtsi, rk3588s.dtsi, rk3588-base.dtsi,
#     rk3588-opp.dtsi, rk3588-base-pinctrl.dtsi, rockchip-pinconf.dtsi,
#     and all dt-bindings headers)

# 2. Preprocess with clang
clang -E -nostdinc -undef -x assembler-with-cpp \
    -I <headers-dir> \
    rk3588s-orangepi-5.dtsi 2>/dev/null | grep -v '^#' > pp.dts

# 3. Replace (~0) with 0xffffffff (THERMAL_NO_LIMIT macro residue)
sed 's/(~0)/0xffffffff/g' pp.dts > flat.dts

# 4. Compile to DTB and decompile back to resolve arithmetic expressions
dtc -I dts -O dtb -f flat.dts -o board.dtb
dtc -I dtb -O dts board.dtb -o data/examples/rk3588s-orangepi-5.dts
```
