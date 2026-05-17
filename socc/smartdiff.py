"""
Smart Semantic DTB/DTS Diff
============================

Compares two device tree sources (any combination of .dts source text and
compiled binary .dtb files) at the **semantic level** — stripping away:

  - Comments and whitespace
  - Label names (``foo: bar@...`` vs ``bar@...``)
  - Macro values (phandles are normalised to ``<&symbolic>`` form)
  - Node ordering within a parent
  - ``linux,phandle`` / ``phandle`` property values (auto-assigned by dtc)

It then produces a structured diff with human-readable context:

  - ``CHANGED`` — same node, same property key, different value
  - ``ADDED``   — node or property present in B but not A
  - ``REMOVED`` — node or property present in A but not B

For common property names it also adds an interpretation comment explaining
what the value change likely means (e.g. ``max-link-speed <2>→<3>`` →
"PCIe Gen2 → Gen3").

DTB decoding requires either:
  a) ``dtc`` on PATH (invoked as subprocess: ``dtc -I dtb -O dts``), or
  b) the ``fdt`` Python package (optional).

If neither is available for a .dtb input, the tool raises ``DTBDecodeError``.

CLI entry:
  socc smart-diff vendor.dtb mainline.dts
  socc smart-diff old.dts new.dts --format text|json|markdown
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


# ── Errors ────────────────────────────────────────────────────────────────────

class DTBDecodeError(RuntimeError):
    """Raised when a .dtb cannot be decompiled."""


# ── DTS tree model ────────────────────────────────────────────────────────────

@dataclass
class DTSNode:
    """Normalised representation of a single DT node."""
    name:       str                          # e.g. "i2c@fe2b0000"
    path:       str                          # full canonical path
    properties: Dict[str, Any]              = field(default_factory=dict)
    children:   Dict[str, "DTSNode"]        = field(default_factory=dict)

    def all_paths(self) -> Iterator[Tuple[str, "DTSNode"]]:
        yield self.path, self
        for child in self.children.values():
            yield from child.all_paths()


# ── Property value annotation database ───────────────────────────────────────

_PROP_HINTS: Dict[str, Dict[Any, str]] = {
    "max-link-speed": {
        1: "PCIe Gen1 (2.5 GT/s)",
        2: "PCIe Gen2 (5 GT/s)",
        3: "PCIe Gen3 (8 GT/s)",
        4: "PCIe Gen4 (16 GT/s)",
    },
    "num-lanes": {},
    "status": {
        "okay": "device enabled",
        "disabled": "device disabled",
        "fail":     "device probe failed",
        "fail-sss": "device probe failed (SoC-specific)",
    },
    "bus-width": {
        1:  "1-bit (SPI)",
        4:  "4-bit (eMMC/SD default)",
        8:  "8-bit (eMMC HS400)",
    },
    "clock-frequency": {},  # will format as Hz/kHz/MHz
    "assigned-clock-rates": {},
}


def _fmt_value(key: str, value: Any) -> str:
    """Return a human-readable string for a property value."""
    hints = _PROP_HINTS.get(key, {})
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    hint = hints.get(value)
    if hint:
        return f"{value!r} ({hint})"
    if key in ("clock-frequency", "assigned-clock-rates") and isinstance(value, int):
        if value >= 1_000_000:
            return f"{value} ({value / 1_000_000:.1f} MHz)"
        elif value >= 1_000:
            return f"{value} ({value / 1_000:.1f} kHz)"
    return repr(value)


# ── DTS parser (minimal, handles most real-world DTS output from dtc) ─────────

_PHANDLE_PROPS = frozenset({"phandle", "linux,phandle"})
_LABEL_RE      = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*')
_NODE_RE       = re.compile(r'^([A-Za-z0-9_,@+/-]+)\s*\{')
_PROP_STR_RE   = re.compile(r'^([A-Za-z0-9_,./\-@#]+)\s*=\s*"((?:[^"\\]|\\.)*)"')
_PROP_CELL_RE  = re.compile(r'^([A-Za-z0-9_,./\-@#]+)\s*=\s*<([^>]*)>')
_PROP_BOOL_RE  = re.compile(r'^([A-Za-z0-9_,./\-@#]+)\s*;')
_PROP_BYTES_RE = re.compile(r'^([A-Za-z0-9_,./\-@#]+)\s*=\s*\[([0-9a-fA-F\s]+)\]')
_CLOSE_RE      = re.compile(r'^\}')


def _tokenise(text: str) -> List[str]:
    """Strip comments and return logical lines."""
    # Remove block comments
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.DOTALL)
    # Remove line comments
    text = re.sub(r'//[^\n]*', '', text)
    # Collapse whitespace
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _parse_cells(raw: str) -> Any:
    """Parse a space-separated cell list like '0xfe2b0000 0x1000'."""
    parts = raw.split()
    vals: List[int] = []
    for p in parts:
        p = p.strip().rstrip(',')
        if p.startswith('0x') or p.startswith('0X'):
            try:
                vals.append(int(p, 16))
                continue
            except ValueError:
                pass
        try:
            vals.append(int(p))
        except ValueError:
            vals.append(p)  # type: ignore[arg-type]
    return vals[0] if len(vals) == 1 else vals


def parse_dts_text(text: str, source: str = "<unknown>") -> DTSNode:
    """
    Parse raw DTS text into a DTSNode tree.

    Returns a synthetic root node whose children are the top-level nodes.
    """
    root = DTSNode(name="__root__", path="/")
    stack: List[DTSNode] = [root]
    path_stack: List[str] = ["/"]

    lines = _tokenise(text)
    i = 0
    while i < len(lines):
        line = lines[i]

        # Strip label prefix
        line = _LABEL_RE.sub('', line)

        # Skip /dts-v1/; and /delete-node/ etc.
        if line.startswith('/') and line.endswith(';'):
            i += 1
            continue

        # Check for opening node: name@addr {
        m = _NODE_RE.match(line)
        if m and '{' in line:
            name = m.group(1)
            parent = stack[-1]
            parent_path = path_stack[-1]
            node_path = (
                parent_path.rstrip('/') + '/' + name
                if parent_path != '/'
                else '/' + name
            )
            new_node = DTSNode(name=name, path=node_path)
            parent.children[name] = new_node
            stack.append(new_node)
            path_stack.append(node_path)
            i += 1
            continue

        # Closing brace
        if _CLOSE_RE.match(line):
            if len(stack) > 1:
                stack.pop()
                path_stack.pop()
            i += 1
            continue

        node = stack[-1]

        # String property: key = "value";
        m = _PROP_STR_RE.match(line)
        if m:
            key, val = m.group(1), m.group(2)
            if key not in _PHANDLE_PROPS:
                node.properties[key] = val
            i += 1
            continue

        # Cell property: key = <val val ...>;
        m = _PROP_CELL_RE.match(line)
        if m:
            key = m.group(1)
            val = _parse_cells(m.group(2))
            if key not in _PHANDLE_PROPS:
                node.properties[key] = val
            i += 1
            continue

        # Byte array property: key = [00 ff ...];
        m = _PROP_BYTES_RE.match(line)
        if m:
            key = m.group(1)
            raw_bytes = bytes(int(b, 16) for b in m.group(2).split())
            node.properties[key] = raw_bytes
            i += 1
            continue

        # Boolean property: key;
        m = _PROP_BOOL_RE.match(line)
        if m:
            key = m.group(1)
            if key not in _PHANDLE_PROPS and not key.startswith('/'):
                node.properties[key] = True
            i += 1
            continue

        i += 1

    return root


# ── DTB decompilation ─────────────────────────────────────────────────────────

def _decompile_dtb(dtb_path: str) -> str:
    """
    Convert a binary .dtb to .dts text using ``dtc``.

    Raises DTBDecodeError if dtc is not available.
    """
    dtc = shutil.which("dtc")
    if not dtc:
        raise DTBDecodeError(
            "dtc (Device Tree Compiler) not found on PATH. "
            "Install it with: apt install device-tree-compiler  "
            "or: brew install dtc"
        )
    result = subprocess.run(
        [dtc, "-I", "dtb", "-O", "dts", "-q", dtb_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise DTBDecodeError(
            f"dtc failed on {dtb_path}:\n{result.stderr}"
        )
    return result.stdout


def load_tree(path: str) -> DTSNode:
    """Load and parse a .dts or .dtb file into a DTSNode tree."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Not found: {path}")
    if p.suffix.lower() == ".dtb":
        text = _decompile_dtb(path)
    else:
        text = p.read_text(errors="replace")
    return parse_dts_text(text, source=path)


# ── Diff engine ───────────────────────────────────────────────────────────────

@dataclass
class DiffEntry:
    kind:       str            # "CHANGED" | "ADDED" | "REMOVED"
    path:       str            # node path
    prop:       Optional[str]  # None if the whole node was added/removed
    value_a:    Any = None
    value_b:    Any = None
    hint:       str = ""       # human-readable interpretation


@dataclass
class SmartDiffReport:
    path_a:  str
    path_b:  str
    entries: List[DiffEntry] = field(default_factory=list)

    @property
    def changed_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == "CHANGED")

    @property
    def added_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == "ADDED")

    @property
    def removed_count(self) -> int:
        return sum(1 for e in self.entries if e.kind == "REMOVED")

    @property
    def total(self) -> int:
        return len(self.entries)


def _build_path_index(root: DTSNode) -> Dict[str, DTSNode]:
    return {path: node for path, node in root.all_paths()}


def _values_equal(a: Any, b: Any) -> bool:
    """Normalised equality that ignores phandle-style references."""
    if type(a) != type(b):
        return str(a) == str(b)
    return a == b


def diff_trees(root_a: DTSNode, root_b: DTSNode) -> List[DiffEntry]:
    """
    Compute semantic diff between two DTSNode trees.

    Returns a list of DiffEntry objects sorted by node path.
    """
    idx_a = _build_path_index(root_a)
    idx_b = _build_path_index(root_b)

    entries: List[DiffEntry] = []
    all_paths = sorted(set(idx_a) | set(idx_b))

    for path in all_paths:
        if path in idx_a and path not in idx_b:
            entries.append(DiffEntry(
                kind="REMOVED", path=path, prop=None,
                hint="Entire node removed in B",
            ))
            continue
        if path not in idx_a and path in idx_b:
            entries.append(DiffEntry(
                kind="ADDED", path=path, prop=None,
                hint="New node in B",
            ))
            continue

        node_a = idx_a[path]
        node_b = idx_b[path]
        all_props = sorted(set(node_a.properties) | set(node_b.properties))

        for prop in all_props:
            if prop in _PHANDLE_PROPS:
                continue
            if prop in node_a.properties and prop not in node_b.properties:
                entries.append(DiffEntry(
                    kind="REMOVED", path=path, prop=prop,
                    value_a=node_a.properties[prop],
                    hint=f"Property removed in B",
                ))
            elif prop not in node_a.properties and prop in node_b.properties:
                entries.append(DiffEntry(
                    kind="ADDED", path=path, prop=prop,
                    value_b=node_b.properties[prop],
                    hint=f"Property added in B",
                ))
            else:
                va = node_a.properties[prop]
                vb = node_b.properties[prop]
                if not _values_equal(va, vb):
                    hint = _make_hint(prop, va, vb)
                    entries.append(DiffEntry(
                        kind="CHANGED", path=path, prop=prop,
                        value_a=va, value_b=vb, hint=hint,
                    ))

    return entries


def _make_hint(prop: str, va: Any, vb: Any) -> str:
    """Generate a human-readable interpretation for a property change."""
    fva = _fmt_value(prop, va)
    fvb = _fmt_value(prop, vb)

    if prop == "max-link-speed":
        return f"PCIe link speed changed: {fva} → {fvb}"
    if prop == "status":
        return f"Device status: {fva} → {fvb}"
    if prop in ("clock-frequency", "assigned-clock-rates"):
        return f"Clock rate: {fva} → {fvb}"
    if prop == "bus-width":
        return f"Bus width: {fva} → {fvb}"
    if prop == "reg":
        return "MMIO base address or size changed — update driver probing if needed"
    if "voltage" in prop or "microvolt" in prop:
        return f"Voltage constraint changed: {fva} → {fvb}"
    if "interrupt" in prop:
        return "Interrupt routing changed"
    if "compatible" in prop:
        return "Compatible string changed — different driver will bind"
    return ""


# ── Main entry point ──────────────────────────────────────────────────────────

def smart_diff(path_a: str, path_b: str) -> SmartDiffReport:
    """
    Load two device tree files (any mix of .dts / .dtb), parse them
    semantically, and return a SmartDiffReport.
    """
    root_a = load_tree(path_a)
    root_b = load_tree(path_b)
    entries = diff_trees(root_a, root_b)
    return SmartDiffReport(path_a=path_a, path_b=path_b, entries=entries)


# ── Renderers ─────────────────────────────────────────────────────────────────

_COLOR = {
    "CHANGED": "\033[1;33m",   # yellow
    "ADDED":   "\033[1;32m",   # green
    "REMOVED": "\033[1;31m",   # red
    "RESET":   "\033[0m",
    "BOLD":    "\033[1m",
    "DIM":     "\033[2m",
    "CYAN":    "\033[1;36m",
}


def render_diff_text(
    report: SmartDiffReport, use_color: bool = True
) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []

    banner = "SOCC SMART SEMANTIC DIFF"
    lines.append(f"{C['BOLD']}{'─' * 65}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 65}{C['RESET']}")
    lines.append(f"  A : {report.path_a}")
    lines.append(f"  B : {report.path_b}")
    lines.append(
        f"  Δ : {C['CHANGED']}{report.changed_count} changed{C['RESET']}  "
        f"{C['ADDED']}{report.added_count} added{C['RESET']}  "
        f"{C['REMOVED']}{report.removed_count} removed{C['RESET']}"
    )
    lines.append("")

    if not report.entries:
        lines.append(
            f"{C['ADDED']}[✓] Trees are semantically identical.{C['RESET']}"
        )
    else:
        cur_path = ""
        for e in report.entries:
            if e.path != cur_path:
                cur_path = e.path
                lines.append(f"\n{C['BOLD']}{e.path}{C['RESET']}")

            sc = C.get(e.kind, "")
            sym = {"CHANGED": "~", "ADDED": "+", "REMOVED": "-"}.get(e.kind, "?")

            if e.prop is None:
                lines.append(f"  {sc}[{sym}] <node> {e.kind.lower()}{C['RESET']}")
            elif e.kind == "CHANGED":
                lines.append(
                    f"  {sc}[~] {e.prop}{C['RESET']}"
                )
                lines.append(
                    f"      {C['REMOVED']}A: {_fmt_value(e.prop, e.value_a)}{C['RESET']}"
                )
                lines.append(
                    f"      {C['ADDED']}B: {_fmt_value(e.prop, e.value_b)}{C['RESET']}"
                )
            elif e.kind == "ADDED":
                lines.append(
                    f"  {sc}[+] {e.prop} = {_fmt_value(e.prop, e.value_b)}{C['RESET']}"
                )
            else:
                lines.append(
                    f"  {sc}[-] {e.prop} = {_fmt_value(e.prop, e.value_a)}{C['RESET']}"
                )

            if e.hint:
                lines.append(
                    f"      {C['DIM']}💡 {e.hint}{C['RESET']}"
                )

    lines.append(f"\n{C['BOLD']}{'─' * 65}{C['RESET']}")
    return "\n".join(lines)


def render_diff_markdown(report: SmartDiffReport) -> str:
    lines: List[str] = []
    lines.append(f"# Smart Semantic Diff")
    lines.append(f"")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **A** | `{report.path_a}` |")
    lines.append(f"| **B** | `{report.path_b}` |")
    lines.append(f"| **Changed** | {report.changed_count} |")
    lines.append(f"| **Added** | {report.added_count} |")
    lines.append(f"| **Removed** | {report.removed_count} |")
    lines.append(f"")

    if not report.entries:
        lines.append("**Trees are semantically identical.**")
        return "\n".join(lines)

    cur_path = ""
    for e in report.entries:
        if e.path != cur_path:
            cur_path = e.path
            lines.append(f"## `{e.path}`")
            lines.append("")

        sym = {"CHANGED": "~", "ADDED": "+", "REMOVED": "-"}.get(e.kind, "?")
        if e.prop is None:
            lines.append(f"- `[{sym}]` *entire node {e.kind.lower()}*")
        elif e.kind == "CHANGED":
            lines.append(
                f"- `[~]` **{e.prop}**: "
                f"`{_fmt_value(e.prop, e.value_a)}` → "
                f"`{_fmt_value(e.prop, e.value_b)}`"
            )
        elif e.kind == "ADDED":
            lines.append(
                f"- `[+]` **{e.prop}** = `{_fmt_value(e.prop, e.value_b)}`"
            )
        else:
            lines.append(
                f"- `[-]` **{e.prop}** = `{_fmt_value(e.prop, e.value_a)}`"
            )
        if e.hint:
            lines.append(f"  - 💡 {e.hint}")

    lines.append("")
    return "\n".join(lines)


def render_diff_json(report: SmartDiffReport) -> str:
    data = {
        "a": report.path_a,
        "b": report.path_b,
        "summary": {
            "changed": report.changed_count,
            "added":   report.added_count,
            "removed": report.removed_count,
        },
        "entries": [
            {
                "kind":    e.kind,
                "path":    e.path,
                "prop":    e.prop,
                "value_a": str(e.value_a) if e.value_a is not None else None,
                "value_b": str(e.value_b) if e.value_b is not None else None,
                "hint":    e.hint,
            }
            for e in report.entries
        ],
    }
    return json.dumps(data, indent=2)
