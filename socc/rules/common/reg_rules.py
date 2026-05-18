"""MM-006 — Register address / node-name mismatch.

A DTS node named ``i2c@fe2b0000`` declares its hardware address in the node
name *and* in the ``reg`` property.  When the two values disagree (typically
from a copy-paste error or a typo in the address), the kernel binds the driver
to the wrong MMIO window.  The node compiles without warnings but the driver
either fails to probe or silently corrupts adjacent memory.

Examples of bugs this catches:
  - ``i2c@fe2b0000`` with ``reg = <0x0 0xfe2c0000 0x0 0x1000>``  (off-by-one)
  - ``uart@feb00000`` with ``reg = <0xfeb000000 0x100>``         (extra zero)
  - ``spi@fe610000``  with ``reg = <0x0 0xfe160000 0x0 0x1000>``  (digits swapped)
"""

from __future__ import annotations

import re
from typing import List

from socc.model import SoC, Violation
from socc.rules.base import BaseRule, CheckContext

# Matches the hex suffix in a node name like "i2c@fe2b0000" or "spi@0,fe610000"
# We take only the LAST hex segment (after the final comma or the @).
_ADDR_RE = re.compile(r'@(?:[0-9a-fA-F]+,)*([0-9a-fA-F]+)$')


def _addr_from_name(node_name: str) -> int | None:
    """Extract the base address encoded in a node name, or None."""
    # Strip trailing path segments if present
    base = node_name.split('/')[-1]
    m = _ADDR_RE.search(base)
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None


def _addr_from_reg(reg_val) -> int | None:
    """Extract the first physical base address from a reg property value.

    reg can be:
      <addr size>              → [addr, size]
      <0x0 addr 0x0 size>     → [0, addr, 0, size]  (64-bit cells)
    """
    if reg_val is None:
        return None
    if isinstance(reg_val, int):
        return reg_val
    if not isinstance(reg_val, (list, tuple)) or len(reg_val) == 0:
        return None
    # If 4-cell form [hi, lo, size_hi, size_lo] where hi==0: address is reg[1]
    if len(reg_val) >= 2 and isinstance(reg_val[0], int) and reg_val[0] == 0:
        if isinstance(reg_val[1], int):
            return reg_val[1]
    # Default: first element is the address
    if isinstance(reg_val[0], int):
        return reg_val[0]
    return None


class RegNameMismatchRule(BaseRule):
    """MM-006: Register address / node-name mismatch."""

    code        = "MM-006"
    name        = "Register Address / Node-Name Mismatch"
    severity    = "error"
    description = (
        "The hexadecimal address suffix in the node name (e.g. i2c@fe2b0000) "
        "does not match the base address declared in the reg property. "
        "This is almost always a copy-paste or typo error."
    )

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        violations: List[Violation] = []

        for dev_name, dev_node in model.devices.items():
            name_addr = _addr_from_name(dev_name)
            if name_addr is None:
                continue  # node name has no @hex suffix — skip

            reg_val  = dev_node.properties.get("reg")
            if reg_val is None:
                continue  # no reg property — skip

            reg_addr = _addr_from_reg(reg_val)
            if reg_addr is None:
                continue

            if name_addr != reg_addr:
                violations.append(self._create_violation(
                    message=(
                        f"Node is named {dev_name!r} (address 0x{name_addr:08x}) "
                        f"but its reg property declares base address "
                        f"0x{reg_addr:08x}."
                    ),
                    impact=(
                        "The kernel driver will be mapped to the wrong MMIO "
                        "window.  The peripheral will either fail to probe or "
                        "corrupt adjacent memory regions."
                    ),
                    suggestion=(
                        f"Fix the mismatch — either rename the node to "
                        f"match{dev_name.split('@')[0]}@{reg_addr:x}, or correct "
                        f"the reg property to <… 0x{name_addr:08x} …>."
                    ),
                    location=f"/{dev_name}",
                    affected_nodes=[dev_name],
                ))

        return violations


def register_reg_rules(registry, soc_name: str = "common") -> None:
    registry.register(RegNameMismatchRule(), soc_name)
