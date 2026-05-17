# Design

## Overview

SoC-Consistency is a rule-based checker that validates the system-level
configuration of a Linux Device Tree.  It complements dt-schema by focusing
on engineering correctness rather than schema compliance.

The key insight is that a DTS file can be fully schema-valid and yet describe
a board that cannot boot: a missing regulator, an incorrect clock rate, a
duplicated GPIO assignment, or a broken interrupt chain.  These problems are
invisible to a schema validator because they require cross-node, cross-subsystem
reasoning against SoC datasheet constraints.

---

## Pipeline

```
DTS text
  |
  v
DTSTokenizer          -- lexical analysis (tokens)
  |
  v
DTSParser             -- recursive descent parser (raw dict tree)
  |
  v
DTSMapper             -- semantic extraction (SoC model)
  |
  +-- PowerTree        (regulators, supply edges, power domains)
  +-- ClockTree        (providers, clocks, parent chains)
  +-- devices          (Dict[str, IRNode])
  +-- device_supplies  (Dict[str, List[str]])
  +-- device_clocks    (Dict[str, List[str]])
  |
  v
RuleRegistry.execute_all()    -- run all registered rules
  |
  v
List[Violation]
  |
  v
Checker.generate_report()     -- text or JSON output
```

---

## Module Map

```
socc/
  cli.py             Command-line interface (Click) — 43 commands
  __main__.py        Allows python -m socc
  parser/
    dts_parser.py    DTSTokenizer, DTSParser, parse_dts()
    dts_mapper.py    DTSMapper, dts_to_soc()
    __init__.py      parse_dts_file() convenience wrapper
  model/
    base.py          IRNode, Violation, Device
    clock.py         Clock, ClockProvider, ClockTree
    power.py         Regulator, PowerTree
    soc.py           SoC (root model object)
    thermal.py       ThermalZone, TripPoint
  rules/
    base.py          BaseRule (ABC), CheckContext
    registry.py      RuleRegistry
    common/          Cross-SoC rules (BW, PIN, SEC, THM, NET, ...)
    rockchip/        Rockchip-specific rules (PD, CK, PIN, MEM, BUS, IRQ)
    allwinner/       Allwinner-specific rules (AW-xxx)
    amlogic/         Amlogic-specific rules (ML-xxx)
    qualcomm/        Qualcomm-specific rules (QC-xxx)
    nxp/             NXP i.MX-specific rules (IMX-xxx)
  engine/
    checker.py       Checker (orchestrates check + report generation)
  memmap.py          MMIO sweep-line overlap scanner (MM-xxx rules)
  depgraph.py        Power/clock DFS cycle and orphan detector (DG-xxx)
  smartdiff.py       Semantic DTS/DTB differ (ignores labels/phandles)
  gc.py              Zombie-node garbage collector
  bounds.py          Physical resource bounds auditor (BND-xxx)
  irqcheck.py        IRQ collision and routing checker (IRQ-Cxx)
  autofix.py         DTS patch generator
  report.py          HTML report renderer
  compliance.py      Kernel compatibility checker
  kernel_audit.py    Cross-check DTS against running kernel drivers
```

---

## Data Model

### IRNode

Every parsed DTS node is stored as an `IRNode`:

```python
@dataclass
class IRNode:
    name: str                        # e.g. "i2c0"
    path: str                        # e.g. "/soc/i2c@fac0000"
    properties: Dict[str, Any]
    children: List[IRNode]
    parent: Optional[IRNode]
```

### SoC

The top-level model object:

```python
@dataclass
class SoC:
    name: str
    power_tree:     PowerTree
    clock_tree:     ClockTree
    devices:        Dict[str, IRNode]         # keyed by node name
    device_supplies: Dict[str, List[str]]     # device -> list of supply names
    device_clocks:  Dict[str, List[str]]      # device -> list of clock refs
```

### PowerTree

Directed graph of `Regulator` nodes connected by supply edges.
Supports cycle detection (DFS) and orphan detection.

### ClockTree

Collection of `ClockProvider` objects and `Clock` objects.
Supports path-to-root traversal, cycle detection, and dangling-parent
detection.

---

## Rule Engine

Each rule is a subclass of `BaseRule`:

```python
class BaseRule(ABC):
    rule_id:   str   # e.g. "PD-001"
    name:      str
    severity:  str   # "error" | "warning" | "info"
    soc_types: List[str]  # ["rk3588", ...] or ["*"] for all SoCs

    @abstractmethod
    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        ...
```

`RuleRegistry` holds all registered rules keyed by `(soc_name, rule_id)`.
`execute_all(model, soc_name, context)` runs every rule that applies to the
given SoC and aggregates the resulting `Violation` list.

---

## Parser Limitations

The DTS parser is a hand-written recursive-descent parser that handles the DTS
text format after C preprocessor expansion.  It does not perform preprocessing
itself.  Files containing `#include` or `/include/` directives must be
preprocessed (e.g. with `clang -E` and optionally compiled and decompiled with
`dtc`) before being passed to the parser.

The test fixture `data/examples/rk3588s-orangepi-5.dts` was produced exactly
this way from the mainline Linux kernel source.

---

## Extensibility

### Adding a new rule

1. Create a class in the appropriate `socc/rules/<vendor>/` module.
2. Inherit from `BaseRule` and implement `check()`.
3. Register it in `socc/rules/<vendor>/__init__.py` via
   `registry.register(MyRule(), soc_name)`.

### Adding a new SoC

1. Create `socc/rules/<vendor>/` with an `__init__.py` that exposes
   `register_<vendor>_rules(registry, soc_name)`.
2. Add a YAML constraint file under `data/soc/<vendor>/<soc>.yaml` with
   voltage ranges, clock limits, etc.
3. Register the new SoC name in `socc/cli.py`.
