"""Clock tree model."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class ClockProvider:
    """Clock provider (PLL, CRU, etc.)."""

    name: str  # provider name
    type: str  # "pll", "cru", "fixed", or "gate"
    base_addr: Optional[int] = None  # base address
    outputs: List[str] = field(default_factory=list)  # output clock names
    parent: Optional[str] = None  # parent provider


@dataclass
class Clock:
    """A single clock signal."""

    name: str  # clock name
    rate: float  # frequency in Hz
    provider: str  # provider name
    parent: Optional[str] = None  # parent clock name
    consumers: List[str] = field(default_factory=list)  # consumer device names


class ClockTree:
    """Clock tree (DAG structure)."""

    def __init__(self):
        self.providers: Dict[str, ClockProvider] = {}
        self.clocks: Dict[str, Clock] = {}
        self.root_clocks: List[str] = []  # clocks with no parent
        self.clock_parents: Dict[str, str] = {}  # clock_name -> parent_clock_name

    def add_provider(self, provider: ClockProvider) -> None:
        """Add a clock provider."""
        if provider.name in self.providers:
            raise ValueError(f"Clock provider {provider.name} already exists")
        self.providers[provider.name] = provider

    def add_clock(self, clock: Clock) -> None:
        """Add a clock."""
        if clock.name in self.clocks:
            raise ValueError(f"Clock {clock.name} already exists")
        self.clocks[clock.name] = clock
        if clock.parent is None:
            self.root_clocks.append(clock.name)
        else:
            self.clock_parents[clock.name] = clock.parent

    def find_provider(self, clock_name: str) -> Optional[str]:
        """Return the provider name for *clock_name*, or None."""
        if clock_name not in self.clocks:
            return None
        return self.clocks[clock_name].provider

    def find_path_to_root(self, clock_name: str) -> List[str]:
        """Return the path from *clock_name* to the root clock."""
        if clock_name not in self.clocks:
            return [clock_name]

        path = [clock_name]
        current = clock_name
        visited: Set[str] = {clock_name}

        while True:
            parent = self.clock_parents.get(current)
            if parent is None:
                break  # reached root
            if parent in visited:
                # cycle detected
                path.append(f"<cycle: {parent}>")
                break
            path.append(parent)
            visited.add(parent)
            current = parent

        return path

    def detect_cycles(self) -> List[List[str]]:
        """Detect cycles in the clock tree and return all cycle paths."""
        cycles: List[List[str]] = []

        def find_cycle_dfs(clock_name: str, visited: Set[str], path: List[str]) -> None:
            """DFS helper to find a cycle starting from *clock_name*."""
            if clock_name in visited:
                # cycle found
                cycle_start = path.index(clock_name)
                cycle = path[cycle_start:] + [clock_name]
                if cycle not in cycles:
                    cycles.append(cycle)
                return

            visited.add(clock_name)
            path.append(clock_name)

            parent = self.clock_parents.get(clock_name)
            if parent:
                find_cycle_dfs(parent, visited.copy(), path[:])

        for clock_name in self.clocks:
            find_cycle_dfs(clock_name, set(), [])

        return cycles

    def validate_connectivity(self) -> List[str]:
        """Return names of all orphaned clocks (cannot reach a root clock)."""
        orphans = []
        for clock_name in self.clocks:
            path = self.find_path_to_root(clock_name)
            # cycle marker means orphaned
            if any("<cycle" in item for item in path):
                orphans.append(clock_name)
            # dangling parent reference
            elif path[-1] not in self.clocks:
                orphans.append(clock_name)

        return orphans

    def is_provider_orphaned(self, provider_name: str) -> bool:
        """Return True if the provider has no output clocks and no consumers."""
        if provider_name not in self.providers:
            return False

        provider = self.providers[provider_name]
        has_outputs = len(provider.outputs) > 0
        # check if any of this provider's clocks have consumers
        has_consumers = any(
            c
            for clock in self.clocks.values()
            if clock.provider == provider_name
            for c in clock.consumers
        )
        return not (has_outputs or has_consumers)

    def get_all_orphaned_providers(self) -> List[str]:
        """Return names of all orphaned clock providers."""
        return [name for name in self.providers 
                if self.is_provider_orphaned(name)]
