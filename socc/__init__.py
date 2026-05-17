"""socc — SoC device-tree consistency checker."""

from .model import (
    IRNode,
    Violation,
    SoC,
    PowerTree,
    Regulator,
    ClockTree,
    ClockProvider,
    Clock,
)
from .rules import BaseRule, CheckContext, RuleRegistry
from .engine import Checker

__version__ = "1.2.0"

__all__ = [
    # Model
    "IRNode",
    "Violation",
    "SoC",
    "PowerTree",
    "Regulator",
    "ClockTree",
    "ClockProvider",
    "Clock",
    # Rules
    "BaseRule",
    "CheckContext",
    "RuleRegistry",
    # Engine
    "Checker",
]
