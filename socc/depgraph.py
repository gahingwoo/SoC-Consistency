"""
Power & Clock Dependency Graph Analyser
========================================

Constructs a directed graph from the SoC model's supply chain and clock tree,
then runs:

  1. **Cycle detection** (DFS-based): finds dependency loops that will deadlock
     the kernel's regulator/clock frameworks at boot.

  2. **Orphan node detection**: supply or clock nodes that are listed as a
     consumer dependency but have no defined source — will cause driver probe
     failures with ``-EPROBE_DEFER`` loops.

  3. **Fan-out anomaly detection**: a single regulator powering an unusually
     large number of consumers (risk of inrush current / sequencing races).

     Threshold defaults to 16 consumers; configurable.

CLI entry:
  socc check-deps board.dts
  socc check-deps board.dts --fan-out-limit 8
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Set, Tuple

from socc.model import SoC


# ── Graph representation ──────────────────────────────────────────────────────

@dataclass
class DepNode:
    name:   str
    kind:   str   # "supply" | "clock" | "device"
    domain: str   # "power" | "clock"


@dataclass
class DepIssue:
    severity:    str
    rule_id:     str
    description: str
    path:        List[str]   # nodes involved (cycle path or orphan reference)
    suggestion:  str


@dataclass
class DepGraphReport:
    nodes:         List[DepNode]  = field(default_factory=list)
    issues:        List[DepIssue] = field(default_factory=list)
    fatal_count:   int = 0
    error_count:   int = 0
    warning_count: int = 0

    @property
    def pass_result(self) -> bool:
        return self.fatal_count == 0 and self.error_count == 0


# ── Graph builder ─────────────────────────────────────────────────────────────

class DirectedGraph:
    """Minimal adjacency-list directed graph."""

    def __init__(self) -> None:
        self._adj: Dict[str, Set[str]] = defaultdict(set)
        self._nodes: Set[str] = set()

    def add_node(self, name: str) -> None:
        self._nodes.add(name)
        if name not in self._adj:
            self._adj[name] = set()

    def add_edge(self, src: str, dst: str) -> None:
        """Edge: src depends-on dst  (src consumes dst's output)."""
        self.add_node(src)
        self.add_node(dst)
        self._adj[src].add(dst)

    @property
    def nodes(self) -> Set[str]:
        return self._nodes

    def successors(self, node: str) -> Set[str]:
        return self._adj.get(node, set())

    def predecessors(self, node: str) -> List[str]:
        return [n for n, nbrs in self._adj.items() if node in nbrs]

    def fan_out(self, node: str) -> int:
        """How many nodes directly depend on ``node``."""
        return sum(1 for _, nbrs in self._adj.items() if node in nbrs)

    # ── Algorithms ────────────────────────────────────────────────────────────

    def find_cycles(self) -> List[List[str]]:
        """
        Return all elementary cycles as lists of node names (Johnson's algorithm
        simplified to DFS + back-edge detection for practical DTS sizes).

        Each returned list represents a cycle: [A, B, C] means A→B→C→A.
        """
        visited:  Set[str] = set()
        rec_stack: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)
            for nbr in sorted(self._adj.get(node, [])):
                if nbr not in visited:
                    dfs(nbr)
                elif nbr in rec_stack:
                    # Back-edge found → extract cycle
                    idx = rec_stack.index(nbr)
                    cycle = rec_stack[idx:] + [nbr]
                    # Deduplicate: normalise to start from lexicographically
                    # smallest node to avoid reporting same cycle twice.
                    if not _cycle_already_seen(cycle, cycles):
                        cycles.append(cycle)
            rec_stack.pop()

        for node in sorted(self._nodes):
            if node not in visited:
                dfs(node)
        return cycles

    def topological_order(self) -> Optional[List[str]]:
        """Kahn's algorithm. Returns None if a cycle exists."""
        in_degree: Dict[str, int] = {n: 0 for n in self._nodes}
        for src in self._nodes:
            for dst in self._adj.get(src, []):
                in_degree[dst] = in_degree.get(dst, 0) + 1

        queue: deque = deque(n for n, d in in_degree.items() if d == 0)
        order: List[str] = []
        while queue:
            n = queue.popleft()
            order.append(n)
            for nbr in sorted(self._adj.get(n, [])):
                in_degree[nbr] -= 1
                if in_degree[nbr] == 0:
                    queue.append(nbr)
        return order if len(order) == len(self._nodes) else None


def _normalise_cycle(cycle: List[str]) -> Tuple[str, ...]:
    """Rotate cycle to start at the lexicographically smallest node."""
    if not cycle:
        return ()
    # cycle = [A, B, C, A] — last element is repeat of first
    body = cycle[:-1]
    min_idx = body.index(min(body))
    rotated = body[min_idx:] + body[:min_idx]
    return tuple(rotated)


def _cycle_already_seen(
    new_cycle: List[str], existing: List[List[str]]
) -> bool:
    norm_new = _normalise_cycle(new_cycle)
    for c in existing:
        if _normalise_cycle(c) == norm_new:
            return True
    return False


# ── SoC graph construction ────────────────────────────────────────────────────

def build_power_graph(soc: SoC) -> DirectedGraph:
    """
    Build a directed graph of power supply dependencies.

    Edge direction: consumer → supplier
    (i.e. "DCDC_A depends on VCC_5V" → edge DCDC_A → VCC_5V)
    """
    g = DirectedGraph()

    # Add all known regulators as nodes
    for name in soc.power_tree.nodes:
        g.add_node(name)

    # Edges from parent relationship in regulator model
    for name, reg in soc.power_tree.nodes.items():
        parent = getattr(reg, "parent", None)
        if parent:
            g.add_edge(name, parent)

    # Edges from power_tree.edges dict {src: [dsts]} — if present
    for src, dsts in (soc.power_tree.edges or {}).items():
        for dst in dsts:
            g.add_edge(src, dst)

    # Also add device supply relationships
    for dev_name, supplies in soc.device_supplies.items():
        for sup in supplies:
            g.add_edge(dev_name, sup)

    return g


def build_clock_graph(soc: SoC) -> DirectedGraph:
    """
    Build a directed graph of clock dependencies.

    Edge direction: consumer → source
    """
    g = DirectedGraph()

    for name, clk in soc.clock_tree.clocks.items():
        g.add_node(name)
        parent = getattr(clk, "parent", None)
        if parent:
            g.add_edge(name, parent)

    for dev_name, clocks in soc.device_clocks.items():
        for clk_name in clocks:
            g.add_edge(dev_name, clk_name)

    return g


# ── Issue checkers ────────────────────────────────────────────────────────────

def check_cycles(
    g: DirectedGraph, domain: str, all_nodes: Set[str]
) -> List[DepIssue]:
    issues: List[DepIssue] = []
    cycles = g.find_cycles()
    for cycle in cycles:
        # Filter: only report cycles that are entirely within supply/clock nodes
        path_str = " → ".join(cycle)
        issues.append(DepIssue(
            severity="FATAL",
            rule_id=f"DG-C{'P' if domain == 'power' else 'K'}01",
            description=(
                f"{'Power supply' if domain == 'power' else 'Clock'} "
                f"dependency cycle detected: {path_str}"
            ),
            path=cycle,
            suggestion=(
                "The Linux regulator/clk framework performs DFS at boot to "
                "build the supply tree. A cycle will cause an infinite loop "
                "in regulator_resolve_supply() or clk_set_parent(), "
                "hanging the system before init runs."
            ),
        ))
    return issues


def check_orphan_supplies(
    soc: SoC, g: DirectedGraph, domain: str
) -> List[DepIssue]:
    """
    Find nodes that reference a supply/clock that does not exist in the model.
    These produce -EPROBE_DEFER loops.
    """
    issues: List[DepIssue] = []
    if domain == "power":
        defined = set(soc.power_tree.nodes.keys())
    else:
        defined = set(soc.clock_tree.clocks.keys())

    for node in sorted(g.nodes):
        for dep in sorted(g.successors(node)):
            if dep not in defined:
                issues.append(DepIssue(
                    severity="ERROR",
                    rule_id=f"DG-O{'P' if domain == 'power' else 'K'}01",
                    description=(
                        f"{'Supply' if domain == 'power' else 'Clock'} "
                        f"reference to undefined node: "
                        f"'{node}' → '{dep}' ('{dep}' not in DTS)"
                    ),
                    path=[node, dep],
                    suggestion=(
                        f"Add a '{dep}' regulator/fixed-clock node, or correct "
                        f"the reference in '{node}'."
                    ),
                ))
    return issues


_DEFAULT_FAN_OUT_LIMIT = 16


def check_fan_out(
    soc: SoC, g: DirectedGraph, domain: str, limit: int = _DEFAULT_FAN_OUT_LIMIT
) -> List[DepIssue]:
    """Warn when a single rail/clock drives more than ``limit`` consumers."""
    issues: List[DepIssue] = []
    if domain == "power":
        source_nodes = set(soc.power_tree.nodes.keys())
    else:
        source_nodes = set(soc.clock_tree.clocks.keys())

    # Build reverse adjacency: supplier → {consumers}
    consumers: Dict[str, Set[str]] = defaultdict(set)
    for node in g.nodes:
        for dep in g.successors(node):
            consumers[dep].add(node)

    for src, cons_set in consumers.items():
        if len(cons_set) > limit:
            issues.append(DepIssue(
                severity="WARNING",
                rule_id=f"DG-F{'P' if domain == 'power' else 'K'}01",
                description=(
                    f"High fan-out: '{src}' ({domain}) drives "
                    f"{len(cons_set)} consumers (limit: {limit})"
                ),
                path=[src] + sorted(cons_set)[:6] + (
                    [f"... +{len(cons_set) - 6} more"] if len(cons_set) > 6 else []
                ),
                suggestion=(
                    "Verify sequencing order and maximum inrush current. "
                    "Consider adding a secondary LDO for isolation."
                ),
            ))
    return issues


# ── Main entry point ──────────────────────────────────────────────────────────

def check_deps(
    soc: SoC, fan_out_limit: int = _DEFAULT_FAN_OUT_LIMIT
) -> DepGraphReport:
    """Full dependency graph audit. Returns a DepGraphReport."""
    report = DepGraphReport()

    pg = build_power_graph(soc)
    cg = build_clock_graph(soc)

    all_issues: List[DepIssue] = []
    all_issues += check_cycles(pg, "power", pg.nodes)
    all_issues += check_cycles(cg, "clock", cg.nodes)
    all_issues += check_orphan_supplies(soc, pg, "power")
    all_issues += check_orphan_supplies(soc, cg, "clock")
    all_issues += check_fan_out(soc, pg, "power", fan_out_limit)
    all_issues += check_fan_out(soc, cg, "clock", fan_out_limit)

    # Collect DepNodes for the report
    for name in pg.nodes:
        report.nodes.append(DepNode(name=name, kind="supply", domain="power"))
    for name in cg.nodes:
        if not any(n.name == name and n.domain == "clock" for n in report.nodes):
            report.nodes.append(DepNode(name=name, kind="clock", domain="clock"))

    _order = {"FATAL": 0, "ERROR": 1, "WARNING": 2, "INFO": 3}
    all_issues.sort(key=lambda i: _order.get(i.severity, 9))
    report.issues = all_issues
    report.fatal_count   = sum(1 for i in all_issues if i.severity == "FATAL")
    report.error_count   = sum(1 for i in all_issues if i.severity == "ERROR")
    report.warning_count = sum(1 for i in all_issues if i.severity == "WARNING")
    return report


# ── Renderer ──────────────────────────────────────────────────────────────────

_COLOR = {
    "FATAL":   "\033[1;35m",
    "ERROR":   "\033[1;31m",
    "WARNING": "\033[1;33m",
    "INFO":    "\033[1;36m",
    "RESET":   "\033[0m",
    "BOLD":    "\033[1m",
    "DIM":     "\033[2m",
    "GREEN":   "\033[1;32m",
}


def render_dep_report(
    report: DepGraphReport, use_color: bool = True
) -> str:
    C = _COLOR if use_color else {k: "" for k in _COLOR}
    lines: List[str] = []

    banner = "SOCC POWER & CLOCK DEPENDENCY GRAPH ANALYSER"
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"{C['BOLD']}{banner}{C['RESET']}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    lines.append(f"  Nodes analysed  : {C['BOLD']}{len(report.nodes)}{C['RESET']}")
    lines.append(
        f"  Issues found    : "
        f"{C['FATAL']}{report.fatal_count} FATAL{C['RESET']}  "
        f"{C['ERROR']}{report.error_count} ERROR{C['RESET']}  "
        f"{C['WARNING']}{report.warning_count} WARNING{C['RESET']}"
    )
    lines.append("")

    if not report.issues:
        lines.append(
            f"{C['GREEN']}[✓] No dependency cycles or orphan references.{C['RESET']}"
        )
    else:
        for issue in report.issues:
            sc = C.get(issue.severity, "")
            lines.append(
                f"{sc}[{issue.severity}] {issue.rule_id}{C['RESET']} "
                f"{issue.description}"
            )
            if issue.path:
                lines.append(
                    f"  {C['DIM']}Path: {' → '.join(issue.path)}{C['RESET']}"
                )
            lines.append(f"  {C['DIM']}→ {issue.suggestion}{C['RESET']}")
            lines.append("")

    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    status = (
        f"{C['GREEN']}PASS{C['RESET']}" if report.pass_result
        else f"{C['FATAL']}FAIL{C['RESET']}"
    )
    lines.append(f"  Result: {status}")
    lines.append(f"{C['BOLD']}{'─' * 60}{C['RESET']}")
    return "\n".join(lines)
