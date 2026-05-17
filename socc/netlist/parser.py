"""CSV netlist parser.

Supports KiCad pin-assignment CSV and Altium netlist CSV exports.

Expected column names (case-insensitive, first matching header wins):

  Pin / PinName / Pad  — physical ball/pad identifier (e.g. A14, PIN_H14, GPIO3_A2)
  Net / NetName / Function / Signal — net / signal name in the schematic

Lines starting with '#' are treated as comments and skipped.
Empty lines and the header row are ignored.
"""

import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class NetlistPin:
    """A single pin entry from a netlist CSV."""

    pin: str           # Physical pad identifier (e.g. "A14", "PIN_H14")
    net: str           # Net / signal name (e.g. "I2C0_SDA", "VDD_CPU")
    pin_type: str = ""  # Optional: "IO", "Power", "Ground", "HighSpeed", …
    ref: str = ""      # Optional: schematic reference designator (e.g. "U1")


# Ordered candidate column names for 'pin' and 'net' fields.
_PIN_HEADERS = ["pin", "pinname", "pad", "ball", "pinno", "pin_name", "padname"]
_NET_HEADERS = ["net", "netname", "function", "signal", "net_name", "signalname"]
_TYPE_HEADERS = ["pintype", "pin_type", "type", "signal_type"]
_REF_HEADERS  = ["ref", "reference", "schref", "component", "designator"]


def _match_header(headers: List[str], candidates: List[str]) -> Optional[int]:
    """Return the index of the first header that matches any candidate (case-insensitive)."""
    lower = [h.lower().replace(" ", "").replace("_", "") for h in headers]
    for cand in candidates:
        cand_norm = cand.lower().replace(" ", "").replace("_", "")
        if cand_norm in lower:
            return lower.index(cand_norm)
    return None


def parse_netlist_csv(path: str) -> List[NetlistPin]:
    """Parse a CSV netlist file and return a list of :class:`NetlistPin` objects.

    Args:
        path: Path to the CSV file.

    Returns:
        List of :class:`NetlistPin` entries (duplicates are kept).

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If required pin/net columns cannot be found in the header.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Netlist CSV not found: {path}")

    # Read file; strip comment lines before passing to csv.reader
    raw_lines: List[str] = []
    with p.open(encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            raw_lines.append(line)

    if not raw_lines:
        return []

    reader = csv.reader(io.StringIO("".join(raw_lines)))
    rows = list(reader)
    if not rows:
        return []

    # Locate header row (first row that contains recognisable column names)
    header = rows[0]
    pin_col = _match_header(header, _PIN_HEADERS)
    net_col = _match_header(header, _NET_HEADERS)

    if pin_col is None or net_col is None:
        # Try auto-detect: assume col 0 = pin, col 1 = net (no-header format)
        try:
            # Validate: first cell should look like a pin (alphanumeric, short)
            if len(rows) >= 1 and rows[0][0] and len(rows[0][0]) <= 20:
                pin_col, net_col = 0, 1
                # Don't skip header row since there is none
            else:
                raise ValueError(
                    f"Cannot locate Pin and Net columns in {path}. "
                    f"Expected headers such as 'Pin,Net' or 'PadName,Signal'."
                )
        except IndexError:
            raise ValueError(f"Cannot parse netlist CSV: {path} appears empty or malformed.")

        data_rows = rows          # no header to skip
    else:
        data_rows = rows[1:]      # skip the header row

    type_col = _match_header(header, _TYPE_HEADERS)
    ref_col  = _match_header(header, _REF_HEADERS)

    pins: List[NetlistPin] = []
    for row in data_rows:
        if not row:
            continue
        try:
            pin_val = row[pin_col].strip()
            net_val = row[net_col].strip()
        except IndexError:
            continue
        if not pin_val or not net_val:
            continue

        pin_type = row[type_col].strip() if type_col is not None and type_col < len(row) else ""
        ref_val  = row[ref_col].strip()  if ref_col  is not None and ref_col  < len(row) else ""

        pins.append(NetlistPin(pin=pin_val, net=net_val, pin_type=pin_type, ref=ref_val))

    return pins


def netlist_to_dict(pins: List[NetlistPin]) -> Dict[str, str]:
    """Convert a list of :class:`NetlistPin` entries to a ``{pin: net}`` mapping.

    If a pin appears more than once the last occurrence wins.
    """
    return {entry.pin: entry.net for entry in pins}
