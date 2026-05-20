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


def _all_addrs_from_reg(reg_val) -> list:
    """Return every physical base address present in a ``reg`` property.

    Handles three common forms used on SoCs:

    * Single integer ``<addr>``
    * 2-cell groups ``<addr size>`` — 1-cell address + 1-cell size (e.g. GRF
      sub-nodes, I2C devices).  First cell of each pair is the address.
    * 4-cell groups ``<hi lo size_hi size_lo> ...`` — 2-cell address + 2-cell
      size.  Only regions where *hi* == 0 are collected (address fits in 32
      bits and can match a 32-bit node-name suffix).

    The cell-width is inferred from the total cell count:
    * ``n % 4 == 0`` → 4-cell groups (64-bit bus, typical root-level SoC nodes)
    * Otherwise        → 2-cell pairs  (32-bit bus or I2C offset)
    """
    if reg_val is None:
        return []
    if isinstance(reg_val, int):
        return [reg_val]
    cells = [c for c in reg_val if isinstance(c, int)]
    if not cells:
        return []
    n = len(cells)
    if n == 1:
        return [cells[0]]
    # 4-cell groups: hi, lo, size_hi, size_lo
    if n % 4 == 0:
        addrs = []
        for i in range(0, n, 4):
            hi, lo = cells[i], cells[i + 1]
            if hi == 0:
                addrs.append(lo)
        return addrs  # empty list is fine — all regions in high memory
    # 2-cell pairs: addr, size
    addrs = []
    for i in range(0, n - 1, 2):
        addrs.append(cells[i])
    return addrs or [cells[0]]



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

            reg_addrs = _all_addrs_from_reg(reg_val)
            if not reg_addrs:
                continue

            if name_addr not in reg_addrs:
                # Report the first address from the reg for context
                reg_addr = reg_addrs[0]
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
