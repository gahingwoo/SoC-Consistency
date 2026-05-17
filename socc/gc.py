"""
DTS Zombie-Node Garbage Collector
===================================

Identifies DTS nodes that are *dead code*: they are disabled and not
transitively referenced by any enabled node via phandle, ``clocks``,
``xxx-supply``, ``pinctrl-*``, ``interrupt-parent``, ``iommus``, etc.

The analyser:
  1. Builds a bi-directional reference graph from the flat devices dict.
  2. Seeds a live set with nodes whose ``status`` is ``"okay"`` or ``"ok"``.
  3. Walks all phandle-style string/list properties to follow references.
  4. Marks everything reachable from the live seed as alive.
  5. Anything left over is a zombie — disabled AND unreachable.

Typical zombie sources:
  - Rockchip/Allwinner vendor BSPs that include every possible camera,
    display, and industrial interface even when not populated.
  - Copy-paste board files that forget to delete unused devices.

CLI entry:
  socc gc board.dts
  socc gc board.dts --format text|json
  socc gc board.dts --threshold 4   # only warn when >= 4 zombies
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set

from socc.model import SoC, IRNode


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ZombieNode:
    name:      str
    path:      str
    status:    str   # "disabled" | "" | "fail" | ...
    ref_count: int   # how many live nodes point to this one
    # Estimated byte size of the compiled node (heuristic: ~64 bytes + props)
    est_bytes: int = 0


@dataclass
class GCReport:
    zombie_nodes:   List[ZombieNode] = field(default_factory=list)
    alive_count:    int = 0
    zombie_count:   int = 0
    est_saved_bytes: int = 0

    @property
    def pass_result(self) -> bool:
        return self.zombie_count == 0


# ── Heuristic node size estimator ────────────────────────────────────────────

def _estimate_node_bytes(node: IRNode) -> int:
    """
    Very rough DTB-encoded size estimate.
    Each node header ≈ 12 bytes, each property ≈ 8 + name_len + value_len bytes.
    """
    size = 12  # FDT_BEGIN_NODE + FDT_END_NODE overhead
    for k, v in node.properties.items():
        size += 8 + len(k.encode()) + 1   # FDT_PROP header + name
        if isinstance(v, str):
            size += len(v.encode()) + 1
        elif isinstance(v, list):
            size += len(v) * 4
        elif isinstance(v, int):
            size += 4
        elif isinstance(v, bytes):
            size += len(v)
        else:
            size += 4
    return size


# ── Phandle-reference extractor ───────────────────────────────────────────────

# Property names that commonly carry phandle references (name → target).
# We look for these plus any property ending in -supply, -gpios, clocks,
# interrupt-parent, iommus, assigned-clocks, pinctrl-*, etc.
_PHANDLE_PROP_PATTERNS = re.compile(
    r"(supply$|gpios?$|clocks?$|interrupt-parent|iommus?|"
    r"power-domains?|resets?$|dmas?$|assigned-clocks?|"
    r"pinctrl-\d+$|port$|ports$|remote-endpoint|phy-handle|"
    r"nvmem-cells?|thermal-sensors?|cooling-device)"
)


def _extract_phandle_names(node: IRNode) -> Set[str]:
    """
    Return a set of node *names* that this node references via phandle
    properties.

    We look for values of the form ``"&some_label"`` (string phandles as
    emitted by some parsers) or bare label strings starting with ``&``.
    """
    refs: Set[str] = set()

    def _scan(val: Any) -> None:
        if isinstance(val, str):
            if val.startswith("&"):
                refs.add(val[1:].split()[0])  # strip trailing junk
        elif isinstance(val, list):
            for item in val:
                _scan(item)

    for prop_name, prop_val in node.properties.items():
        # Always scan any property that looks phandle-ish
        if _PHANDLE_PROP_PATTERNS.search(prop_name):
            _scan(prop_val)
        # Also scan all properties for &label strings
        _scan(prop_val)

    return refs


# ── Reference graph builder ───────────────────────────────────────────────────

def _build_ref_graph(
    devices: Dict[str, IRNode]
) -> Dict[str, Set[str]]:
    """
    Build {node_name: {set of node_names it references}} from the devices dict.

    We also build a reverse map  (referenced_by) to count incoming refs.
    Returns forward adjacency only; callers can invert as needed.
    """
    forward: Dict[str, Set[str]] = {n: set() for n in devices}

    # Also index by path suffix (last component after @) for cross-matching
    path_to_name: Dict[str, str] = {}
    for name, node in devices.items():
        path_to_name[node.path] = name
        # Label index: some parsers emit bare label names
        path_to_name[name] = name

    for name, node in devices.items():
        raw_refs = _extract_phandle_names(node)
        for ref in raw_refs:
            # Match ref against names and path suffixes
            if ref in devices:
                forward[name].add(ref)
            else:
                # Try matching against path last component
                for dev_name, dev_node in devices.items():
                    # e.g. ref="cru" matches node "cru@fd7c0000"
                    if dev_node.name.startswith(ref) or dev_name == ref:
                        forward[name].add(dev_name)
                        break

    return forward


def _compute_live_set(
    devices: Dict[str, IRNode],
    forward: Dict[str, Set[str]],
) -> Set[str]:
    """
    BFS/DFS from all enabled nodes; return set of all reachable node names.
    """
    # Seed: all nodes with status okay/ok, plus the root node
    live: Set[str] = set()
    for name, node in devices.items():
        status = node.properties.get("status", "okay")
        if str(status).lower() in ("okay", "ok"):
            live.add(name)

    # Expand: follow forward references transitively
    queue = list(live)
    while queue:
        current = queue.pop()
        for neighbour in forward.get(current, set()):
            if neighbour not in live:
                live.add(neighbour)
                queue.append(neighbour)

    return live


# ── Main entry point ──────────────────────────────────────────────────────────

def run_gc(soc: SoC) -> GCReport:
    """
    Run the dead-code GC on a SoC model.

    Returns a GCReport listing zombie nodes and estimated byte savings.
    """
    devices = soc.devices
    if not devices:
        return GCReport(alive_count=0, zombie_count=0)

    forward = _build_ref_graph(devices)
    live = _compute_live_set(devices, forward)

    # Build reverse ref-count map
    ref_count: Dict[str, int] = {n: 0 for n in devices}
    for src, dsts in forward.items():
        for dst in dsts:
            if dst in ref_count:
                ref_count[dst] += 1

    report = GCReport()
    report.alive_count = 0

    for name, node in sorted(devices.items()):
        if name in live:
            report.alive_count += 1
        else:
            status = node.properties.get("status", "")
            est = _estimate_node_bytes(node)
            report.zombie_nodes.append(ZombieNode(
                name=name,
                path=node.path,
                status=str(status),
                ref_count=ref_count.get(name, 0),
                est_bytes=est,
            ))

    report.zombie_count = len(report.zombie_nodes)
    report.est_saved_bytes = sum(z.est_bytes for z in report.zombie_nodes)
    return report


# ── Renderer ──────────────────────────────────────────────────────────────────

_COLOR = {
    "WARN":  "\033[1;33m",
    "OK":    "\033[1;32m",
    "RED":   "\033[1;31m",
    "RESET": "\033[0m",
    "BOLD":  "\033[1m",
    "DIM":   "\033[2m",
    "CYAN":  "\033[1;36m",
}


def render_gc_text(report: GCReport, use_color: bool = True) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []

    banner = "SOCC DTS ZOMBIE-NODE GARBAGE COLLECTOR"
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"  Alive nodes   : {C['BOLD']}{report.alive_count}{C['RESET']}")
    lines.append(
        f"  Zombie nodes  : "
        f"{C['RED'] if report.zombie_count else C['OK']}"
        f"{report.zombie_count}{C['RESET']}"
    )
    if report.est_saved_bytes:
        kb = report.est_saved_bytes / 1024
        lines.append(
            f"  Est. DTB savings: {C['CYAN']}{report.est_saved_bytes} bytes"
            f" (~{kb:.1f} KiB){C['RESET']}"
        )
    lines.append("")

    if not report.zombie_nodes:
        lines.append(f"{C['OK']}[✓] No zombie nodes found.{C['RESET']}")
    else:
        lines.append(
            f"{C['WARN']}[CLEANUP] Found {report.zombie_count} unreferenced zombie nodes!{C['RESET']}"
        )
        for z in sorted(report.zombie_nodes, key=lambda n: n.path):
            s_color = C["DIM"] if z.status == "disabled" else C["RED"]
            lines.append(
                f"  {C['DIM']}•{C['RESET']} {z.path}"
            )
            lines.append(
                f"    {s_color}Status: {z.status or '(none)'}{C['RESET']}  "
                f"{C['DIM']}refs: {z.ref_count}  "
                f"~{z.est_bytes} bytes{C['RESET']}"
            )
        lines.append("")
        lines.append(
            f"  {C['DIM']}💡 Remove these nodes to reduce DTB size by "
            f"~{report.est_saved_bytes / 1024:.1f} KiB "
            f"and speed up kernel boot.{C['RESET']}"
        )

    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    return "\n".join(lines)


def render_gc_json(report: GCReport) -> str:
    return json.dumps({
        "alive_count":    report.alive_count,
        "zombie_count":   report.zombie_count,
        "est_saved_bytes": report.est_saved_bytes,
        "zombies": [
            {
                "name":      z.name,
                "path":      z.path,
                "status":    z.status,
                "ref_count": z.ref_count,
                "est_bytes": z.est_bytes,
            }
            for z in report.zombie_nodes
        ],
    }, indent=2)
