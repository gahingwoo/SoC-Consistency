"""ASCII-art power-sequencing waveform renderer.

Usage::

    from socc.visualize.power_seq import render_power_sequence
    print(render_power_sequence(model, use_color=True))

Output example::

    Power Rail Startup Sequence — rk3568-board
    ==========================================
    TIME (µs) →  0     200   400   600   800  1000  2000  5000
                 |     |     |     |     |     |     |     |
    VIN_5V       ████████████████████████████████████████████  5.0V  [fixed]
    DCDC1        ░░░░░░░░░░░░██████████████████████████████   0.9V  [buck]  →VIN_5V
    DCDC2        ░░░░░░░░░░░░░░░░░░░░░░████████████████████   1.8V  [buck]  →VIN_5V
    LDO1         ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████  3.3V  [ldo]   →DCDC2
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from socc.model import SoC
from socc.model.power import Regulator


# ──────────────────────────── colour helpers ────────────────────────────────

def _maybe_style(text: str, use_color: Optional[bool], **kwargs) -> str:
    """Wrap *text* with click.style only when colour is available."""
    if not use_color:
        return text
    try:
        import click
        return click.style(text, **kwargs)
    except Exception:
        return text


# ──────────────────────────── sequencing logic ──────────────────────────────

def _assign_sequence_order(model: SoC) -> None:
    """Assign sequence_order to each Regulator via BFS from root nodes."""
    tree = model.power_tree
    if not tree.root_nodes:
        # Identify roots as nodes with no parents
        for name in list(tree.nodes.keys()):
            if not tree.reverse_edges.get(name):
                tree.root_nodes.append(name)

    visited: Dict[str, int] = {}
    queue: List[Tuple[str, int]] = [(r, 0) for r in tree.root_nodes]

    while queue:
        name, depth = queue.pop(0)
        if name in visited:
            continue
        visited[name] = depth
        reg = tree.nodes.get(name)
        if reg:
            reg.sequence_order = depth
        for child in tree.edges.get(name, []):
            if child not in visited:
                queue.append((child, depth + 1))

    # Assign any remaining nodes that aren't reachable from declared roots
    for name, reg in tree.nodes.items():
        if name not in visited:
            reg.sequence_order = 999


def _compute_startup_time_us(reg: Regulator, depth: int) -> int:
    """Return cumulative startup time in µs for a regulator at *depth*."""
    # Base: each depth level adds 200 µs minimum
    base_us = depth * 200
    return base_us + reg.startup_delay_us + reg.ramp_delay_us


# ──────────────────────────── waveform drawing ──────────────────────────────

_TIMELINE_TICKS = [0, 200, 400, 600, 800, 1000, 2000, 5000, 10000]
_BAR_CHAR   = "█"
_RAMP_CHAR  = "░"
_BLANK_CHAR = " "
_BAR_WIDTH  = 48     # characters for the full timeline


def _time_to_col(t_us: int, max_t: int) -> int:
    """Map time *t_us* to a column index [0, _BAR_WIDTH]."""
    if max_t <= 0:
        return 0
    ratio = min(t_us / max_t, 1.0)
    return int(ratio * _BAR_WIDTH)


def render_power_sequence(
    model: SoC,
    use_color: Optional[bool] = None,
    title: Optional[str] = None,
) -> str:
    """Render an ASCII-art power-startup waveform for *model*.

    Args:
        model: Populated SoC model.
        use_color: Whether to emit ANSI colour codes (None = auto-detect).
        title: Optional diagram title (defaults to model.name).

    Returns:
        Multi-line string ready for ``print()``.
    """
    if use_color is None:
        try:
            import click
            use_color = True
        except ImportError:
            use_color = False

    _assign_sequence_order(model)
    tree = model.power_tree

    if not tree.nodes:
        return f"No power regulators found in model for {model.name}."

    # Sort regulators by sequence_order then name for stable output
    sorted_regs: List[Regulator] = sorted(
        tree.nodes.values(), key=lambda r: (r.sequence_order, r.name)
    )

    # Calculate per-regulator startup time
    startup_times: Dict[str, int] = {}
    for reg in sorted_regs:
        t = _compute_startup_time_us(reg, reg.sequence_order)
        startup_times[reg.name] = t

    max_time = max(startup_times.values(), default=0) + 500
    max_time = max(max_time, 1000)  # at least 1 ms range

    # ── header ────────────────────────────────────────────────────────────
    title_str = title or f"Power Rail Startup Sequence — {model.name}"
    sep       = "=" * len(title_str)
    lines: List[str] = [
        _maybe_style(title_str, use_color, bold=True),
        sep,
        "",
    ]

    # ── timeline ruler ────────────────────────────────────────────────────
    name_w = max(len(r.name) for r in sorted_regs) + 2
    ticks  = [t for t in _TIMELINE_TICKS if t <= max_time]
    if not ticks or ticks[-1] < max_time:
        ticks.append(max_time)

    ruler_top  = " " * name_w
    ruler_mid  = " " * name_w
    for t in ticks:
        col  = _time_to_col(t, max_time)
        label = f"{t}"
        # pad to next tick column
        ruler_top += label.ljust(8)
        ruler_mid += "|".ljust(8)

    lines.append(_maybe_style("TIME (µs) →  " + ruler_top[len("TIME (µs) →  "):], use_color, dim=True))
    lines.append(_maybe_style(" " * name_w + ruler_mid[name_w:], use_color, dim=True))
    lines.append("")

    # ── per-rail rows ─────────────────────────────────────────────────────
    for reg in sorted_regs:
        t_start = startup_times[reg.name]
        ramp_us = reg.ramp_delay_us or 50  # minimum ramp width for visibility

        # bar segments: blank → ramp → solid
        col_start = _time_to_col(t_start, max_time)
        col_ramp  = _time_to_col(t_start + ramp_us, max_time)

        bar = (
            _BLANK_CHAR * col_start
            + _RAMP_CHAR * max(col_ramp - col_start, 1)
            + _BAR_CHAR  * (_BAR_WIDTH - col_ramp)
        )
        # Colour the bar based on voltage class
        v_mid = (reg.voltage_min + reg.voltage_max) / 2
        if v_mid >= 3.0:
            bar_colored = _maybe_style(bar, use_color, fg="green")
        elif v_mid >= 1.5:
            bar_colored = _maybe_style(bar, use_color, fg="cyan")
        else:
            bar_colored = _maybe_style(bar, use_color, fg="yellow")

        # Right-side annotation
        parent_str = f"  →{reg.parent}" if reg.parent else ""
        delay_str  = f"  +{t_start}µs" if t_start else ""
        v_str      = f"  {reg.voltage_min:.1f}V" if reg.voltage_min == reg.voltage_max \
                     else f"  {reg.voltage_min:.1f}–{reg.voltage_max:.1f}V"
        current_str = f"  max {reg.max_current_ma}mA" if reg.max_current_ma > 0 else ""
        annotation = _maybe_style(
            f"{v_str}  [{reg.type}]{parent_str}{delay_str}{current_str}",
            use_color, dim=True
        )

        name_col = _maybe_style(reg.name.ljust(name_w), use_color, bold=True)
        lines.append(f"{name_col}{bar_colored}{annotation}")

    lines.append("")
    lines.append(
        _maybe_style(
            "Legend: " + _maybe_style("█ active", use_color, fg="green")
            + "  " + _RAMP_CHAR + " ramp  " + _BLANK_CHAR + " off",
            use_color, dim=True
        )
    )
    lines.append(
        _maybe_style(
            "Colors: green ≥3V (IO/DDR)  cyan 1.5–2.9V  yellow <1.5V (core)",
            use_color, dim=True,
        )
    )

    return "\n".join(lines)
