"""Rule registry: registration, lookup, and bulk execution."""

from typing import Callable, Dict, List

from socc.model import SoC, Violation

from .base import BaseRule, CheckContext


class RuleRegistry:
    """Rule registry managing rule sets per SoC."""

    def __init__(self):
        self._rules: Dict[str, BaseRule] = {}  # rule code -> rule object
        self._soc_rules: Dict[str, List[BaseRule]] = {}  # SoC name -> rule list
        self._registrars: Dict[str, Callable] = {}  # SoC name -> registrar function

    def register(self, rule: BaseRule, soc_name: str = "common") -> None:
        """Register a single rule.

        Args:
            rule: Rule instance to register.
            soc_name: Target SoC name; use "common" for universal rules.
        """
        if not rule.code:
            raise ValueError(f"Rule {rule.__class__.__name__} has no code defined")

        if soc_name not in self._soc_rules:
            self._soc_rules[soc_name] = []

        # duplicate check
        for existing_rule in self._soc_rules[soc_name]:
            if existing_rule.code == rule.code:
                raise ValueError(
                    f"Rule code {rule.code} already registered for {soc_name}"
                )

        self._soc_rules[soc_name].append(rule)
        self._rules[rule.code] = rule

    def get_rules_for_soc(self, soc_name: str) -> List[BaseRule]:
        """Return all rules applicable to *soc_name* (common + SoC-specific)."""
        rules = []

        # common rules
        if "common" in self._soc_rules:
            rules.extend(self._soc_rules["common"])

        # SoC-specific rules
        if soc_name in self._soc_rules:
            rules.extend(self._soc_rules[soc_name])

        return rules

    def register_soc_rules(
        self, soc_name: str, registrar: Callable[["RuleRegistry", str], None]
    ) -> None:
        """Register all rules for a SoC via a registrar callable.

        Args:
            soc_name: SoC name string.
            registrar: Callable with signature registrar(registry, soc_name).
        """
        if soc_name in self._registrars:
            raise ValueError(f"Registrar for SoC {soc_name} already exists")

        self._registrars[soc_name] = registrar
        # execute registrar immediately
        registrar(self, soc_name)

    def execute_all(
        self, model: SoC, soc_name: str, context: CheckContext = None
    ) -> List[Violation]:
        """Execute all rules applicable to *soc_name* and return sorted violations."""
        if context is None:
            context = CheckContext(soc_name=soc_name)

        all_violations: List[Violation] = []
        rules = self.get_rules_for_soc(soc_name)

        for rule in rules:
            try:
                violations = rule.check(model, context)
                all_violations.extend(violations)
            except Exception as e:
                # log rule failure and continue
                print(f"Warning: rule {rule.code} raised an exception: {e}")

        # sort by severity: error -> warning -> info
        severity_order = {"error": 0, "warning": 1, "info": 2}
        all_violations.sort(
            key=lambda v: (severity_order.get(v.severity, 99), v.code)
        )

        return all_violations

    def get_rule(self, code: str) -> BaseRule:
        """Return the rule for *code*, raising ValueError if not found."""
        if code not in self._rules:
            raise ValueError(f"Rule code {code!r} is not registered")
        return self._rules[code]

    def list_all_rules(self) -> List[BaseRule]:
        """Return a list of all registered rules."""
        return list(self._rules.values())
