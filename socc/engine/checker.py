"""SoC consistency checking engine."""

import json
from typing import List, Optional

import click

from socc.model import SoC, Violation

from socc.rules import RuleRegistry, CheckContext


class Checker:
    """Orchestrates rule execution and report generation."""

    def __init__(self, registry: RuleRegistry):
        """Initialize the checker with a rule registry."""
        self.registry = registry

    def check(self, model: SoC, soc_name: str, extra_metadata: Optional[dict] = None) -> List[Violation]:
        """Run all rules against *model* and return sorted violations."""
        metadata = dict(extra_metadata) if extra_metadata else {}
        context = CheckContext(soc_name=soc_name, metadata=metadata)
        violations = self.registry.execute_all(model, soc_name, context)
        return violations

    def generate_report(
        self,
        violations: List[Violation],
        output_format: str = "text",
        color: Optional[bool] = None,
    ) -> str:
        """Generate a formatted report for *violations*.

        Args:
            violations: List of Violation objects.
            output_format: "text", "json", or "sarif".
            color: Force ANSI color on (True), off (False), or auto-detect (None).

        Returns:
            Formatted report string.
        """
        if output_format == "json":
            return self._generate_json_report(violations)
        elif output_format == "sarif":
            return self._generate_sarif_report(violations)
        else:
            return self._generate_text_report(violations, color=color)

    # ------------------------------------------------------------------
    # Color helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _styled(text: str, color: Optional[bool], **kwargs) -> str:
        """Apply click.style unless color is explicitly disabled."""
        if color is False:
            return text
        return click.style(text, **kwargs)

    def _sev_label(self, severity: str, color: Optional[bool]) -> str:
        """Return a colored (or plain) severity label string."""
        _MAP = {
            "error":   ("[ERROR]  ", "red",    True),
            "warning": ("[WARNING]", "yellow", False),
            "info":    ("[INFO]   ", "cyan",   False),
        }
        label, fg, bold = _MAP.get(severity, ("[?]     ", "white", False))
        return self._styled(label, color, fg=fg, bold=bold)

    # ------------------------------------------------------------------

    def _generate_text_report(
        self, violations: List[Violation], color: Optional[bool] = None
    ) -> str:
        """Generate a plain-text (optionally colored) report."""
        if not violations:
            return self._styled(
                "All checks passed. No violations found.",
                color, fg="green", bold=True
            )

        lines = []
        lines.append("=" * 80)
        lines.append("SoC Consistency Check Report")
        lines.append("=" * 80)
        lines.append(f"\nFound {len(violations)} violation(s):\n")

        # group by severity
        errors = [v for v in violations if v.severity == "error"]
        warnings = [v for v in violations if v.severity == "warning"]
        infos = [v for v in violations if v.severity == "info"]

        def _section(header: str, items: list, fg: str) -> None:
            if not items:
                return
            lines.append(self._styled(header, color, fg=fg, bold=True))
            lines.append("-" * 80)
            for v in items:
                tag = self._sev_label(v.severity, color)
                lines.append(f"{tag} [{v.code}] {v.message}")
                if v.location:
                    lines.append(f"         Location : {v.location}" +
                                 (f":{v.line}" if v.line else ""))
                if v.impact:
                    lines.append(f"         Impact   : {v.impact}")
                if v.suggestion:
                    lines.append(f"         Fix      : {v.suggestion}")
                lines.append("")

        _section(f"\nErrors ({len(errors)}):", errors, "red")
        _section(f"\nWarnings ({len(warnings)}):", warnings, "yellow")
        _section(f"\nInfo ({len(infos)}):", infos, "cyan")

        lines.append("=" * 80)
        err_str = self._styled(f"{len(errors)} error(s)", color, fg="red", bold=True)
        warn_str = self._styled(f"{len(warnings)} warning(s)", color, fg="yellow")
        info_str = self._styled(f"{len(infos)} info", color, fg="cyan")
        lines.append(f"\nSummary: {err_str}, {warn_str}, {info_str}")
        lines.append("=" * 80)

        return "\n".join(lines)

    def _generate_json_report(self, violations: List[Violation]) -> str:
        """Generate a JSON report."""
        report = {
            "summary": {
                "total": len(violations),
                "errors": len([v for v in violations if v.severity == "error"]),
                "warnings": len([v for v in violations if v.severity == "warning"]),
                "infos": len([v for v in violations if v.severity == "info"]),
            },
            "violations": [v.to_dict() for v in violations],
        }
        return json.dumps(report, indent=2)

    def _generate_sarif_report(self, violations: List[Violation]) -> str:
        """Generate a SARIF 2.1.0 report for GitHub Code Scanning."""
        _level = {"error": "error", "warning": "warning", "info": "note"}

        # collect unique rules for the driver
        rules_seen: dict = {}
        for v in violations:
            if v.code not in rules_seen:
                rules_seen[v.code] = {
                    "id": v.code,
                    "name": v.rule_name or v.code,
                    "shortDescription": {"text": v.message},
                    "helpUri": f"https://github.com/your-org/soc-consistency/blob/main/docs/rules.md#{v.code.lower().replace('-', '')}",
                }

        results = []
        for v in violations:
            result: dict = {
                "ruleId": v.code,
                "level": _level.get(v.severity, "note"),
                "message": {"text": v.message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": v.location or "unknown"},
                            **(
                                {"region": {"startLine": v.line}}
                                if v.line
                                else {}
                            ),
                        }
                    }
                ],
            }
            results.append(result)

        sarif = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "socc",
                            "version": "0.2.0",
                            "informationUri": "https://github.com/your-org/soc-consistency",
                            "rules": list(rules_seen.values()),
                        }
                    },
                    "results": results,
                }
            ],
        }
        return json.dumps(sarif, indent=2)
