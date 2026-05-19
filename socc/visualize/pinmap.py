"""BGA pinout heatmap generator.

Reads BGA ball-assignment data from a SoC YAML constraint file and generates
a self-contained HTML file showing a colour-coded pin map.

YAML BGA section format
-----------------------
::

    bga:
      package: FCBGA-533
      rows: [A, B, C, D, E, F, G, H, J, K, L, M, N, P, R, T, U, V, W, Y, Z, AA, AB]
      cols: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
      pins:
        A1:  { type: power,  name: VDD_CPU }
        A2:  { type: ground, name: GND }
        B3:  { type: gpio,   name: GPIO0_A0,  bank: 0, group: A, idx: 0 }
        C4:  { type: io,     name: I2C0_SDA,  function: I2C }
        D5:  { type: hs_io,  name: USB3_TX_P, function: USB3 }
        E6:  { type: nc,     name: NC }

Pin types and their colours
---------------------------
power    → red       (#e74c3c)
ground   → dark grey (#2c3e50)
gpio     → green     (#27ae60)
io       → blue      (#2980b9)    (low-speed: I2C, SPI, UART, PWM …)
hs_io    → purple    (#8e44ad)    (high-speed: PCIe, USB3, MIPI, GMAC …)
ddr      → orange    (#e67e22)
audio    → teal      (#16a085)
jtag     → yellow    (#f1c40f)
nc       → light grey (#bdc3c7)
unknown  → white     (#ecf0f1)
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


# ────────────────────────────── colour map ──────────────────────────────────

_TYPE_COLORS: Dict[str, Tuple[str, str]] = {
    # type         → (background, text)
    "power":   ("#e74c3c", "#ffffff"),
    "ground":  ("#2c3e50", "#ffffff"),
    "gpio":    ("#27ae60", "#ffffff"),
    "io":      ("#2980b9", "#ffffff"),
    "hs_io":   ("#8e44ad", "#ffffff"),
    "ddr":     ("#e67e22", "#ffffff"),
    "audio":   ("#16a085", "#ffffff"),
    "jtag":    ("#f1c40f", "#2c3e50"),
    "nc":      ("#bdc3c7", "#7f8c8d"),
    "unknown": ("#ecf0f1", "#7f8c8d"),
}

_TYPE_LABELS: Dict[str, str] = {
    "power":   "PWR",
    "ground":  "GND",
    "gpio":    "GPIO",
    "io":      "IO",
    "hs_io":   "HS",
    "ddr":     "DDR",
    "audio":   "AUD",
    "jtag":    "JTAG",
    "nc":      "NC",
    "unknown": "?",
}


def _load_bga_data(soc_name: str, data_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load BGA pin data from the YAML constraint file for *soc_name*.

    Looks in ``{data_dir}/soc/{vendor}/{soc_name}.yaml`` using a vendor heuristic.

    Returns the ``bga`` section dict, or empty dict if not found.
    """
    soc_lower = soc_name.lower()

    # Validate to prevent path traversal (OWASP A01)
    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', soc_lower):
        return {}

    if data_dir is None:
        # Primary: inside the socc package (included in wheels via package-data)
        here = Path(__file__).parent  # socc/visualize/
        pkg_data = here.parent / "data"  # socc/data/
        if not pkg_data.exists():
            # Fallback: project-root data/ (editable installs, development)
            pkg_data = here.parent.parent / "data"
        data_dir = str(pkg_data)

    # vendor heuristic from SoC name prefix
    if soc_lower.startswith("rk") or soc_lower.startswith("px"):
        vendor = "rockchip"
    elif soc_lower.startswith("sun") or soc_lower.startswith("axp"):
        vendor = "allwinner"
    elif soc_lower.startswith("meson") or soc_lower.startswith("amlogic"):
        vendor = "amlogic"
    elif soc_lower.startswith("sdm") or soc_lower.startswith("sm") \
            or soc_lower.startswith("sc") or soc_lower.startswith("qcs") \
            or soc_lower.startswith("msm"):
        vendor = "qualcomm"
    else:
        vendor = soc_lower  # fall back: look for same-named folder

    yaml_path = Path(data_dir) / "soc" / vendor / f"{soc_lower}.yaml"
    if not yaml_path.exists():
        return {}

    with yaml_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    return data.get("bga", {})


# ─────────────────────────────── HTML template ──────────────────────────────

_HTML_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee;
           padding: 20px; }}
    h1   {{ font-size: 1.4rem; margin-bottom: 4px; }}
    p.sub {{ font-size: 0.85rem; color: #aaa; margin-bottom: 16px; }}
    .grid-wrapper {{ overflow-x: auto; }}
    .grid {{
      display: grid;
      gap: 3px;
      grid-template-columns: 24px {col_template};
    }}
    .cell {{
      width: 36px; height: 36px;
      display: flex; align-items: center; justify-content: center;
      border-radius: 50%;
      font-size: 7px; font-weight: 600;
      cursor: default;
      transition: transform .1s;
      border: 1px solid rgba(255,255,255,0.1);
    }}
    .cell:hover {{ transform: scale(1.4); z-index: 10; position: relative; }}
    .header-cell {{
      width: 36px; height: 36px; display: flex; align-items: center;
      justify-content: center; font-size: 9px; font-weight: bold;
      color: #aaa; border-radius: 4px;
    }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }}
    .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; }}
    .legend-dot {{ width: 18px; height: 18px; border-radius: 50%; }}
    .stats {{ margin: 12px 0; font-size: 0.82rem; color: #aaa; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p class="sub">Package: {package} &nbsp;|&nbsp; Total balls: {total} &nbsp;|&nbsp;
     Defined: {defined} &nbsp;|&nbsp; NC: {nc_count}</p>
  <div class="stats">{stats_html}</div>
  <div class="grid-wrapper">
  <div class="grid">
"""

_HTML_FOOT = """\
  </div><!-- .grid -->
  </div><!-- .grid-wrapper -->
  <div class="legend">
{legend_html}
  </div>
</body>
</html>
"""


def _make_legend_html() -> str:
    items = []
    label_names = {
        "power": "Power rail", "ground": "Ground", "gpio": "GPIO",
        "io": "Low-speed IO", "hs_io": "High-speed IO", "ddr": "DDR",
        "audio": "Audio", "jtag": "JTAG/Debug", "nc": "No Connect", "unknown": "Unknown",
    }
    for typ, (bg, fg) in _TYPE_COLORS.items():
        label = label_names.get(typ, typ)
        items.append(
            f'    <div class="legend-item">'
            f'<div class="legend-dot" style="background:{bg};"></div>'
            f'<span>{html.escape(label)}</span></div>'
        )
    return "\n".join(items)


def render_bga_heatmap(
    soc_name: str,
    output_path: str,
    data_dir: Optional[str] = None,
    dts_pinmux: Optional[Dict[str, str]] = None,
) -> str:
    """Generate an HTML BGA heatmap for *soc_name* and write it to *output_path*.

    Args:
        soc_name: SoC identifier (e.g. ``"rk3568"``).
        output_path: Destination HTML file path.
        data_dir: Override for the ``data/`` directory.  Auto-detected when *None*.
        dts_pinmux: Optional ``{pin: function}`` dict from DTS mapper — used to
                    cross-reference and highlight misconfigured pads.

    Returns:
        Path to the generated HTML file (same as *output_path*).

    Raises:
        ValueError: If no BGA data is available for *soc_name*.
    """
    bga_data = _load_bga_data(soc_name, data_dir)

    if not bga_data:
        raise ValueError(
            f"No BGA layout data found for '{soc_name}'. "
            f"Add a 'bga:' section to the corresponding YAML file in data/soc/."
        )

    rows: List[str] = bga_data.get("rows", [])
    cols: List[Any] = bga_data.get("cols", [])
    pins_data: Dict[str, Any] = bga_data.get("pins", {})
    package: str = bga_data.get("package", "N/A")

    if not rows or not cols:
        raise ValueError(f"BGA data for '{soc_name}' is missing 'rows' or 'cols' lists.")

    dts_pinmux = dts_pinmux or {}

    # ── statistics ────────────────────────────────────────────────────────
    total  = len(rows) * len(cols)
    defined = sum(1 for v in pins_data.values() if v and v.get("type", "unknown") != "unknown")
    nc_count = sum(1 for v in pins_data.values() if v and v.get("type") == "nc")

    type_counts: Dict[str, int] = {}
    for v in pins_data.values():
        t = (v or {}).get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    stats_parts = [f"{t}: {c}" for t, c in sorted(type_counts.items())]
    stats_html  = html.escape("  |  ".join(stats_parts))

    # ── build grid HTML ───────────────────────────────────────────────────
    col_template = " ".join(["36px"] * len(cols))
    title = f"{soc_name.upper()} BGA Pinout Heatmap"

    head = _HTML_HEAD.format(
        title=html.escape(title),
        col_template=col_template,
        package=html.escape(package),
        total=total,
        defined=defined,
        nc_count=nc_count,
        stats_html=stats_html,
    )

    cells: List[str] = []

    # Column header row
    cells.append('<div class="header-cell"></div>')  # top-left corner blank
    for col in cols:
        cells.append(f'<div class="header-cell">{html.escape(str(col))}</div>')

    # Data rows
    for row_label in rows:
        # Row label
        cells.append(f'<div class="header-cell">{html.escape(str(row_label))}</div>')
        for col in cols:
            ball_id = f"{row_label}{col}"
            pin_info = pins_data.get(ball_id) or {}
            pin_type = pin_info.get("type", "unknown")
            pin_name = pin_info.get("name", ball_id)

            bg, fg = _TYPE_COLORS.get(pin_type, _TYPE_COLORS["unknown"])
            short   = _TYPE_LABELS.get(pin_type, "?")

            # DTS cross-reference: highlight mismatches
            border_style = ""
            if dts_pinmux and ball_id in dts_pinmux:
                dts_fn = dts_pinmux[ball_id]
                if pin_info and not _functions_match(pin_name, dts_fn):
                    border_style = "border: 2px solid #ff0; box-shadow: 0 0 4px #ff0;"

            tooltip = html.escape(f"{ball_id}: {pin_name}")
            if dts_pinmux and ball_id in dts_pinmux:
                tooltip += html.escape(f"  [DTS: {dts_pinmux[ball_id]}]")

            cells.append(
                f'<div class="cell" title="{tooltip}" '
                f'style="background:{bg};color:{fg};{border_style}">'
                f'{html.escape(short)}</div>'
            )

    legend_html = _make_legend_html()

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        fh.write(head)
        fh.write("\n".join(cells))
        fh.write(_HTML_FOOT.format(legend_html=legend_html))

    return str(out_path)


def _functions_match(pin_name: str, dts_function: str) -> bool:
    """Loose check: does *pin_name* match the DTS function string?"""
    a = pin_name.lower().replace("_", "").replace("-", "")
    b = dts_function.lower().replace("_", "").replace("-", "")
    return a == b or a.startswith(b) or b.startswith(a)
