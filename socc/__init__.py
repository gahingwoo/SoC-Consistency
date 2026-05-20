"""socc — SoC device-tree consistency checker."""

__version__ = "1.4.3"

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
