"""Multi-SKU DTS comparison engine.

``socc audit sku a.dts b.dts [c.dts ...]`` loads multiple DTS files
that represent product variants sharing the same SoC and produces a
side-by-side divergence report.

Differences are classified as:
    CONFLICT   – same node/property path has different values across SKUs
    MISSING    – a node/property present in some SKUs but absent in others
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from socc.model.soc import SoC


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SkuDiff:
    """Result of comparing two or more SoC models."""

    sku_names: List[str]
    """Ordered list of SKU names (filenames)."""

    conflicts: List["SkuConflict"] = field(default_factory=list)
    """Property values that differ across SKUs."""

    missing: List["SkuMissing"] = field(default_factory=list)
    """Nodes/properties present in some SKUs but absent in others."""

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts) + len(self.missing)


@dataclass
class SkuConflict:
    """A property whose value differs across SKUs."""

    node: str
    prop: str
    values: Dict[str, Any]  # {sku_name: value}
    severity: str = "warning"


@dataclass
class SkuMissing:
    """A node/property present in some SKUs but absent in others."""

    node: str
    prop: Optional[str]  # None → entire node is missing
    present_in: List[str]
    absent_in: List[str]
    severity: str = "warning"


# ── Comparison logic ──────────────────────────────────────────────────────────

def compare_sku_models(models: Dict[str, SoC]) -> SkuDiff:
    """Compare *models* (mapping of filename → SoC model) and return a SkuDiff."""
    sku_names = list(models.keys())
    diff = SkuDiff(sku_names=sku_names)

    # Collect all node names across all models
    all_nodes: set[str] = set()
    for model in models.values():
        all_nodes.update(model.devices.keys())

    for node_name in sorted(all_nodes):
        # Determine which SKUs have this node
        present = [s for s, m in models.items() if node_name in m.devices]
        absent = [s for s in sku_names if s not in present]

        if absent:
            diff.missing.append(
                SkuMissing(
                    node=node_name,
                    prop=None,
                    present_in=present,
                    absent_in=absent,
                )
            )
            continue  # no point comparing properties if node missing

        # Collect all property names for this node across models
        all_props: set[str] = set()
        for sku in sku_names:
            node = models[sku].devices[node_name]
            all_props.update(node.properties.keys())

        for prop in sorted(all_props):
            # Skip internal/structural properties
            if prop in ("#address-cells", "#size-cells", "phandle"):
                continue

            values: Dict[str, Any] = {}
            for sku in sku_names:
                node = models[sku].devices[node_name]
                values[sku] = node.properties.get(prop, _ABSENT)

            # Check for missing in some SKUs
            sku_absent = [s for s, v in values.items() if v is _ABSENT]
            sku_present = [s for s, v in values.items() if v is not _ABSENT]
            if sku_absent and sku_present:
                diff.missing.append(
                    SkuMissing(
                        node=node_name,
                        prop=prop,
                        present_in=sku_present,
                        absent_in=sku_absent,
                    )
                )
                continue

            # Check for conflicts (all present but values differ)
            non_absent = {s: v for s, v in values.items() if v is not _ABSENT}
            unique_vals = {_canonical(v) for v in non_absent.values()}
            if len(unique_vals) > 1:
                diff.conflicts.append(
                    SkuConflict(
                        node=node_name,
                        prop=prop,
                        values=non_absent,
                    )
                )

    return diff


# Sentinel for "property absent"
class _AbsentType:
    def __repr__(self):
        return "<absent>"

_ABSENT = _AbsentType()


def _canonical(val: Any) -> str:
    """Stable string representation for comparison."""
    if isinstance(val, (list, tuple)):
        return repr(sorted(str(x) for x in val))
    return repr(val)


# ── Renderers ─────────────────────────────────────────────────────────────────

def render_sku_table(diff: SkuDiff, *, use_color: Optional[bool] = True) -> str:
    """Render a text table for the terminal."""
    try:
        import click
        _style = click.style
        _can_color = use_color
    except ImportError:
        _style = lambda s, **kw: s
        _can_color = False

    lines: list[str] = []
    header = f"SKU Divergence Report — {len(diff.sku_names)} variants compared"
    lines.append("=" * len(header))
    lines.append(header)
    lines.append("  Variants: " + ", ".join(diff.sku_names))
    lines.append("=" * len(header))
    lines.append("")

    if not diff.conflicts and not diff.missing:
        lines.append(
            _style("  All SKUs are identical — no divergences found.", fg="green")
            if _can_color
            else "  All SKUs are identical — no divergences found."
        )
        return "\n".join(lines) + "\n"

    if diff.conflicts:
        lines.append(
            _style(f"  Conflicts ({len(diff.conflicts)}):", fg="red", bold=True)
            if _can_color
            else f"  Conflicts ({len(diff.conflicts)}):"
        )
        for c in diff.conflicts:
            lines.append(f"    Node: {c.node}  Property: {c.prop}")
            for sku, val in c.values.items():
                lines.append(f"      {sku:<30}  {val!r}")
        lines.append("")

    if diff.missing:
        lines.append(
            _style(f"  Missing nodes/properties ({len(diff.missing)}):", fg="yellow", bold=True)
            if _can_color
            else f"  Missing nodes/properties ({len(diff.missing)}):"
        )
        for m in diff.missing:
            prop_str = f".{m.prop}" if m.prop else ""
            lines.append(
                f"    {m.node}{prop_str}  "
                f"present: [{', '.join(m.present_in)}]  "
                f"absent: [{', '.join(m.absent_in)}]"
            )
        lines.append("")

    summary_color = "red" if diff.conflict_count else "green"
    summary = f"  Summary: {diff.conflict_count} divergence(s) found."
    lines.append(
        _style(summary, fg=summary_color, bold=True) if _can_color else summary
    )
    return "\n".join(lines) + "\n"


def render_sku_json(diff: SkuDiff) -> str:
    """Render a JSON string for machine consumption."""
    out = {
        "sku_names": diff.sku_names,
        "conflict_count": diff.conflict_count,
        "conflicts": [
            {"node": c.node, "prop": c.prop,
             "values": {k: str(v) for k, v in c.values.items()}}
            for c in diff.conflicts
        ],
        "missing": [
            {"node": m.node, "prop": m.prop,
             "present_in": m.present_in, "absent_in": m.absent_in}
            for m in diff.missing
        ],
    }
    return _json.dumps(out, indent=2)
