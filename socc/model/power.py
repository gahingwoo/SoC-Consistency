"""Power tree model."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class Regulator:
    """A voltage regulator node."""

    name: str  # regulator name (e.g., "vdd_cpu")
    type: str  # "fixed", "dcdc", "ldo", or "switch"
    voltage_min: float  # minimum voltage in V
    voltage_max: float  # maximum voltage in V
    consumers: List[str] = field(default_factory=list)  # consumer phandle list
    parent: Optional[str] = None  # parent regulator name
    startup_delay_us: int = 0  # regulator-enable-ramp-delay from DTS (µs)
    ramp_delay_us: int = 0  # regulator-ramp-delay from DTS (µs)
    max_current_ma: int = 0  # max output current from YAML spec (mA); 0 = unspecified
    sequence_order: int = 999  # lower = powered on earlier (derived from tree depth)


class PowerTree:
    """Power tree (directed graph)."""

    def __init__(self):
        self.nodes: Dict[str, Regulator] = {}
        self.edges: Dict[str, List[str]] = {}  # parent -> children
        self.reverse_edges: Dict[str, List[str]] = {}  # child -> parents
        self.root_nodes: List[str] = []  # regulators with no parent

    def add_regulator(self, regulator: Regulator) -> None:
        """Add a regulator node."""
        if regulator.name in self.nodes:
            raise ValueError(f"Regulator {regulator.name} already exists")
        self.nodes[regulator.name] = regulator
        self.edges[regulator.name] = []
        self.reverse_edges[regulator.name] = []

    def add_edge(self, parent: str, child: str) -> None:
        """Add a supply edge: parent powers child."""
        if parent not in self.nodes or child not in self.nodes:
            raise ValueError(f"Node not found: {parent} or {child}")
        if child not in self.edges[parent]:
            self.edges[parent].append(child)
        if parent not in self.reverse_edges[child]:
            self.reverse_edges[child].append(parent)

    def find_supply_chain(self, device_name: str) -> List[str]:
        """Return the full supply chain from device up to the root regulator."""
        if device_name not in self.nodes:
            return [device_name]  # node not in tree

        chain = [device_name]
        current = device_name

        while True:
            parents = self.reverse_edges.get(current, [])
            if not parents:
                break  # reached root
            # take first parent (DAG may have multiple)
            current = parents[0]
            if current in chain:
                # cycle detected, abort
                chain.append(f"<cycle: {current}>")
                break
            chain.append(current)

        return chain

    def detect_cycles(self) -> List[List[str]]:
        """Detect cycles in the power tree and return all cycle paths."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node: str, path: List[str]) -> bool:
            """DFS helper to detect a cycle from *node*."""
            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for child in self.edges.get(node, []):
                if child in rec_stack:
                    # cycle found
                    cycle_start = path.index(child)
                    cycle = path[cycle_start:] + [child]
                    cycles.append(cycle)
                elif child not in visited:
                    dfs(child, path[:])

            rec_stack.remove(node)

        for node in self.nodes:
            if node not in visited:
                dfs(node, [])

        return cycles

    def has_voltage_conflict(self, node1: str, node2: str) -> bool:
        """Return True if the voltage ranges of two regulators overlap."""
        if node1 not in self.nodes or node2 not in self.nodes:
            return False

        reg1 = self.nodes[node1]
        reg2 = self.nodes[node2]

        # check if voltage ranges overlap
        voltage_overlap = not (reg1.voltage_max < reg2.voltage_min or 
                              reg2.voltage_max < reg1.voltage_min)
        return voltage_overlap

    def is_orphaned(self, node_name: str) -> bool:
        """Return True if the regulator has no consumers and no children."""
        if node_name not in self.nodes:
            return False
        reg = self.nodes[node_name]
        # orphaned: no consumers and no children
        has_consumers = len(reg.consumers) > 0
        has_children = len(self.edges.get(node_name, [])) > 0
        return not (has_consumers or has_children)

    def get_all_orphaned(self) -> List[str]:
        """Return names of all orphaned regulators."""
        return [name for name in self.nodes if self.is_orphaned(name)]
