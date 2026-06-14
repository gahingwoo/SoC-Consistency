"""Common memory rules.

Constraint-metadata driven and SoC-agnostic; fires only when the caller
supplies ``memory`` / ``memory_timing`` / ``memory_capacity`` keys.
"""

from typing import List, Dict, Set
from socc.model import SoC
from socc.rules.base import BaseRule, Violation, CheckContext


class MEM301AddressingError(BaseRule):
    """MEM-301: Memory Addressing Error"""

    code = "MEM-301"
    name = "Addressing Error"
    description = "Memory address ranges must be contiguous and non-overlapping."
    severity = "error"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check memory address map for overlaps or gaps.

        Constraint format:
        {
            "memory": {
                "ddr0": {"start": 0x00000000, "size": 0x40000000},
                "ddr1": {"start": 0x40000000, "size": 0x40000000}
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "memory" not in constraints:
            return violations
        
        memory_config = constraints["memory"]
        
        # sort memory blocks by start address
        mem_blocks = []
        for mem_name, config in memory_config.items():
            start = config.get("start", 0)
            size = config.get("size", 0)
            mem_blocks.append((start, start + size, mem_name))
        
        mem_blocks.sort()
        
        # check for overlaps and gaps
        for i in range(len(mem_blocks) - 1):
            end_curr = mem_blocks[i][1]
            start_next = mem_blocks[i + 1][0]
            
            if end_curr > start_next:
                # overlap detected
                violations.append(
                    self._create_violation(
                        message=f"Memory block {mem_blocks[i][2]!r} (0x{mem_blocks[i][0]:X}-0x{end_curr:X}) "
                                f"overlaps {mem_blocks[i+1][2]!r} (0x{start_next:X}-0x{mem_blocks[i+1][1]:X}).",
                        impact="Address decode conflict; data corruption may occur.",
                        suggestion="Adjust start addresses or sizes to eliminate the overlap.",
                        location="/memory",
                        affected_nodes=[mem_blocks[i][2], mem_blocks[i+1][2]],
                    )
                )
            elif end_curr < start_next and (start_next - end_curr) > 0x1000:
                # gap > 4 KB
                violations.append(
                    self._create_violation(
                        message=f"Gap of 0x{start_next - end_curr:X} bytes between "
                                f"{mem_blocks[i][2]!r} and {mem_blocks[i+1][2]!r}.",
                        impact="Wasted address space; low memory utilization.",
                        suggestion="Adjust the memory map to reduce gaps, or document the gap's purpose.",
                        location="/memory",
                        affected_nodes=[mem_blocks[i][2], mem_blocks[i+1][2]],
                    )
                )
        
        return violations


class MEM302TimingConstraint(BaseRule):
    """MEM-302: Timing Constraint Violation"""

    code = "MEM-302"
    name = "Timing Constraint"
    description = "Memory access timing must satisfy SoC specification."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check memory access timing parameters.

        Constraint format:
        {
            "memory_timing": {
                "ddr0": {
                    "tCAS": 20,      # CAS latency (cycles)
                    "tRP": 12,       # Precharge latency
                    "tRCD": 12,      # Row-Column delay
                    "max_freq": 800e6
                }
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "memory_timing" not in constraints:
            return violations
        
        timing_spec = constraints["memory_timing"]
        
        # Demonstration rule; real DTS parsing would extract timing from device tree.
        
        for mem_name, timing in timing_spec.items():
            # validate timing parameters
            tCAS = timing.get("tCAS", 0)
            tRP = timing.get("tRP", 0)
            tRCD = timing.get("tRCD", 0)
            
            # check CAS latency (typically 15-25)
            if tCAS < 10 or tCAS > 30:
                violations.append(
                    self._create_violation(
                        message=f"Memory {mem_name!r} CAS latency {tCAS} is outside range [10, 30].",
                        impact="Timing mismatch may cause access timeouts or data errors.",
                        suggestion="Adjust tCAS: DDR3/4 is typically 15-20, DDR5 is 20-25.",
                        location=f"/memory/{mem_name}",
                        affected_nodes=[mem_name],
                    )
                )
            
            # check row precharge time
            if tRP < 8 or tRP > 20:
                violations.append(
                    self._create_violation(
                        message=f"Memory {mem_name!r} precharge latency {tRP} is out of specification.",
                        impact="Row activation and precharge sequences may fail.",
                        suggestion="Consult the memory datasheet; tRP is typically 12-15.",
                        location=f"/memory/{mem_name}",
                        affected_nodes=[mem_name],
                    )
                )
        
        return violations


class MEM303CapacityAllocation(BaseRule):
    """MEM-303: Memory Capacity Allocation"""

    code = "MEM-303"
    name = "Capacity Allocation"
    description = "Check whether memory capacity allocation is reasonable."
    severity = "warning"

    def check(self, model: SoC, context: CheckContext) -> List[Violation]:
        """
        Check whether memory capacity allocation meets performance requirements.

        Constraint format:
        {
            "memory_capacity": {
                "ddr": 2048,           # DDR capacity in MB
                "sram": 256,           # SRAM capacity in KB
                "min_ddr_required": 1024
            }
        }
        """
        violations: List[Violation] = []
        
        constraints = context.metadata.get("constraints", {})
        if "memory_capacity" not in constraints:
            return violations
        
        capacity = constraints["memory_capacity"]
        
        ddr_mb = capacity.get("ddr", 0)
        sram_kb = capacity.get("sram", 0)
        min_ddr = capacity.get("min_ddr_required", 512)
        
        # check DDR capacity
        if ddr_mb < min_ddr:
            violations.append(
                self._create_violation(
                    message=f"DDR capacity {ddr_mb} MB is below the minimum requirement of {min_ddr} MB.",
                    impact="Insufficient memory; cannot run a complete software stack.",
                    suggestion=f"Increase DDR capacity to at least {min_ddr} MB.",
                    location="/memory/ddr",
                    affected_nodes=["ddr"],
                )
            )
        
        # check SRAM capacity
        if sram_kb < 64:
            violations.append(
                self._create_violation(
                    message=f"SRAM capacity {sram_kb} KB is very small and may impact cache performance.",
                    impact="Low cache hit rate degrades system performance.",
                    suggestion="Increase SRAM capacity; at least 256 KB is recommended for L2 cache.",
                    location="/memory/sram",
                    affected_nodes=["sram"],
                )
            )
        
        # check SRAM/DDR ratio (recommended: 0.5-5%)
        if ddr_mb > 0:
            ratio = sram_kb / (ddr_mb * 1024)
            if ratio < 0.005:
                violations.append(
                    self._create_violation(
                        message=f"SRAM/DDR ratio {ratio*100:.2f}% is too low.",
                        impact="Insufficient fast storage relative to DDR degrades overall performance.",
                        suggestion=f"SRAM should be at least 0.5% of DDR (~{int(ddr_mb*10.24)} KB).",
                        location="/memory",
                        affected_nodes=["sram", "ddr"],
                    )
                )
        
        return violations


def register_common_memory_rules(registry, soc_name: str = "common") -> None:
    """Register common memory rules."""
    registry.register(MEM301AddressingError(), soc_name)
    registry.register(MEM302TimingConstraint(), soc_name)
    registry.register(MEM303CapacityAllocation(), soc_name)
