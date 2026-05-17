"""DTBO property trace engine.

Usage:
    socc trace /soc/i2c@fe2b0000 base.dts [overlay1.dtbo [overlay2.dtbo ...]]

Shows how a given node's properties evolve as overlay files are applied
on top of the base DTS, so you can spot silent overrides.

Output example:

    [TRACE] Property 'status' on /soc/i2c@fe2b0000:
      1. base.dts           : "disabled"
      2. rock-5b.dts        : "okay"        ← last change
      3. camera.dtbo        : "disabled"    ← SILENT OVERRIDE

For every property that changes across the layer stack, the tool marks
the final value and highlights unexpected reversions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from socc.model import SoC


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class PropertyChange:
    """Describes the value of a property at one layer in the stack."""
    layer_index: int
    layer_name: str          # filename (basename)
    layer_path: str          # full path
    value: Any               # the value after this layer
    is_new: bool = False     # first time this property appears
    is_changed: bool = False # different from previous layer


@dataclass
class PropertyTrace:
    """Full trace of a single property across all layers."""
    property_name: str
    node_path: str
    changes: List[PropertyChange] = field(default_factory=list)

    @property
    def final_value(self) -> Any:
        return self.changes[-1].value if self.changes else None

    @property
    def has_silent_override(self) -> bool:
        """True if a later layer reverts a previously set value."""
        if len(self.changes) < 3:
            return False
        # Look for a value that goes A → B → A (or any revert)
        seen: List[Any] = []
        for ch in self.changes:
            if ch.is_changed and seen:
                if ch.value == seen[-2] if len(seen) >= 2 else False:
                    return True
            seen.append(ch.value)
        return False


@dataclass
class TraceReport:
    node_path: str
    layers: List[str]        # ordered list of layer file names
    traces: List[PropertyTrace]


# ── DTS/overlay loading helpers ───────────────────────────────────────────────


def _load_model(dts_path: str, soc_name: str = "generic") -> Optional[SoC]:
    """Parse *dts_path* and return a SoC model, or None on failure."""
    try:
        from socc.parser.dts_parser import DTSParser
        from socc.parser.dts_mapper import DTSMapper
        with open(dts_path, "r", encoding="utf-8") as fh:
            text = fh.read()
        tokens = DTSParser().parse(text)
        mapper = DTSMapper(soc_name=soc_name)
        return mapper.map(tokens)
    except Exception:
        return None


def _find_node(model: SoC, node_path: str) -> Optional[Any]:
    """Walk model.devices to find an IRNode with a matching path."""
    # Normalise: strip trailing slash, ensure leading /
    target = "/" + node_path.strip("/")
    for dev_name, node in model.devices.items():
        if node.path == target or node.path.rstrip("/") == target.rstrip("/"):
            return node
    # Fallback: match by name component
    name_part = target.rsplit("/", 1)[-1]
    for dev_name, node in model.devices.items():
        if dev_name == name_part or node.name == name_part:
            return node
    return None


# ── Core trace logic ──────────────────────────────────────────────────────────


def trace_node(
    node_path: str,
    base_dts: str,
    overlays: List[str],
    soc_name: str = "generic",
) -> TraceReport:
    """
    Load *base_dts* and apply each overlay in order.  At each layer, record
    the property values of the node at *node_path*.

    Returns a TraceReport with per-property change history.
    """
    all_layers = [base_dts] + list(overlays)
    layer_names = [Path(p).name for p in all_layers]

    # Accumulate property snapshots per layer using the overlay merger
    snapshots: List[Tuple[str, Dict[str, Any]]] = []

    # Try to use the OverlayMerger for proper overlay handling
    try:
        from socc.overlay.merger import OverlayMerger
        merger = OverlayMerger(base_dts)
        # Layer 0 — base DTS
        base_model = merger.merged_model(soc_name)
        base_node = _find_node(base_model, node_path)
        snapshots.append((base_dts, dict(base_node.properties) if base_node else {}))

        # Apply overlays one by one
        for ovl_path in overlays:
            merger.add_overlay(ovl_path)
            merged_model = merger.merged_model(soc_name)
            found_node = _find_node(merged_model, node_path)
            snapshots.append((ovl_path, dict(found_node.properties) if found_node else {}))

    except ImportError:
        # OverlayMerger not available — load each file independently
        for layer_path in all_layers:
            model = _load_model(layer_path, soc_name)
            if model:
                node = _find_node(model, node_path)
                snapshots.append((layer_path, dict(node.properties) if node else {}))
            else:
                snapshots.append((layer_path, {}))

    # Build per-property traces
    all_props: Dict[str, List[Tuple[int, str, str, Any]]] = {}
    # all_props[prop] = [(layer_idx, layer_name, layer_path, value), ...]

    for idx, (lpath, props) in enumerate(snapshots):
        lname = Path(lpath).name
        for prop, val in props.items():
            all_props.setdefault(prop, [])
            all_props[prop].append((idx, lname, lpath, val))

    traces: List[PropertyTrace] = []
    for prop_name, entries in sorted(all_props.items()):
        pt = PropertyTrace(property_name=prop_name, node_path=node_path)
        prev_val = None
        prev_set = False

        for layer_idx, lname, lpath, val in entries:
            changed = prev_set and (val != prev_val)
            new     = not prev_set
            pt.changes.append(PropertyChange(
                layer_index=layer_idx,
                layer_name=lname,
                layer_path=lpath,
                value=val,
                is_new=new,
                is_changed=changed,
            ))
            prev_val = val
            prev_set = True

        traces.append(pt)

    return TraceReport(
        node_path=node_path,
        layers=layer_names,
        traces=traces,
    )


# ── Rendering ─────────────────────────────────────────────────────────────────


def render_trace_report(report: TraceReport, use_color: bool = True) -> str:
    """Render the trace report as a human-readable string."""
    import click

    lines: List[str] = []
    header = f"[TRACE] Node: {report.node_path}"
    lines.append(click.style(header, fg="cyan", bold=True) if use_color else header)
    lines.append(f"  Layers applied ({len(report.layers)}): " + " → ".join(report.layers))
    lines.append("")

    if not report.traces:
        msg = "  (node not found in any layer)"
        lines.append(click.style(msg, fg="yellow") if use_color else msg)
        return "\n".join(lines)

    # Show only properties that change across layers (or are interesting)
    interesting = [
        t for t in report.traces
        if any(c.is_changed for c in t.changes)
        or t.property_name in ("status", "compatible", "reg", "clocks", "pinctrl-0")
    ]
    if not interesting:
        interesting = report.traces  # show all if nothing changed

    for pt in interesting:
        prop_header = f"  Property '{pt.property_name}':"
        lines.append(click.style(prop_header, bold=True) if use_color else prop_header)

        for ch in pt.changes:
            val_str = repr(ch.value) if not isinstance(ch.value, str) else f'"{ch.value}"'
            marker = ""
            if ch.is_changed:
                marker = " ← changed"
                if pt.has_silent_override:
                    marker = " ← SILENT OVERRIDE"
            elif ch.is_new:
                marker = " ← first set"

            line = f"    {ch.layer_index + 1}. {ch.layer_name:<30s} : {val_str}"
            if use_color and marker:
                color = "red" if "SILENT" in marker else "yellow"
                line += click.style(marker, fg=color, bold=True)
            else:
                line += marker
            lines.append(line)
        lines.append("")

    return "\n".join(lines)
