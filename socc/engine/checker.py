"""SoC consistency checking engine."""

import json
from collections import defaultdict
from typing import Dict, List, Optional

import click

from socc.model import SoC, Violation

from socc.rules import RuleRegistry, CheckContext

# ── Rule-code prefix → human-readable subsystem name ─────────────────────────
_DOMAIN_MAP: Dict[str, str] = {
    "PD":  "Power",
    "CK":  "Clock",
    "GP":  "GPIO",
    "MM":  "Memory",
    "BND": "Bounds",
    "IRQ": "IRQ",
    "DG":  "DepGraph",
    "TH":  "Thermal",
    "BW":  "Bandwidth",
    "SEC": "Security",
    "NET": "Netlist",
    "KNL": "Kernel",
    "BOM": "BOM",
    "OVL": "Overlay",
    "BUS": "Bus",
    "AMP": "AMP",
    "MTX": "Matrix",
}


def _rule_domain(code: str) -> str:
    """Infer a human-readable subsystem name from a rule code prefix."""
    prefix = code.split("-")[0].upper()
    return _DOMAIN_MAP.get(prefix, "Other")


class Checker:
    """Orchestrates rule execution and report generation."""

    def __init__(self, registry: RuleRegistry):
        """Initialize the checker with a rule registry."""
        self.registry = registry

    def check(
        self,
        model: SoC,
        soc_name: str,
        extra_metadata: Optional[dict] = None,
        source_file: Optional[str] = None,
    ) -> List[Violation]:
        """Run all rules against *model* and return sorted violations.

        If *source_file* is provided every returned :class:`~socc.model.Violation`
        will have its ``source_file`` attribute set so that the caller can render
        Rust-style diagnostics.
        """
        metadata = dict(extra_metadata) if extra_metadata else {}
        context  = CheckContext(soc_name=soc_name, metadata=metadata)
        violations = self.registry.execute_all(model, soc_name, context)
        if source_file:
            for v in violations:
                if v.source_file is None:
                    v.source_file = source_file
        return violations

    def generate_report(
        self,
        violations: List[Violation],
        output_format: str = "text",
        color: Optional[bool] = None,
        source_file: Optional[str] = None,
    ) -> str:
        """Generate a formatted report for *violations*.

        Args:
            violations:    List of Violation objects.
            output_format: "text", "json", "sarif", or "annotations".
            color:         Force ANSI color on (True), off (False), or auto (None).
            source_file:   DTS source path, used for Rust-style diagnostics in
                           text format.

        Returns:
            Formatted report string.
        """
        if output_format == "json":
            return self._generate_json_report(violations)
        elif output_format == "sarif":
            return self._generate_sarif_report(violations)
        elif output_format == "annotations":
            return self._generate_annotations_report(violations)
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
        errors   = [v for v in violations if v.severity == "error"]
        warnings = [v for v in violations if v.severity == "warning"]
        infos    = [v for v in violations if v.severity == "info"]

        def _section(header: str, items: list, fg: str) -> None:
            if not items:
                return
            lines.append(self._styled(header, color, fg=fg, bold=True))
            lines.append("-" * 80)
            for v in items:
                # ── Rust-style header ─────────────────────────────────────
                try:
                    from socc.diagnostics import render_diagnostic_header, render_source_snippet
                    lines.append(render_diagnostic_header(
                        v.code, v.message, color=color, severity=v.severity))
                except ImportError:
                    tag = self._sev_label(v.severity, color)
                    lines.append(f"{tag} [{v.code}] {v.message}")

                # ── location line ─────────────────────────────────────────
                if v.location:
                    loc = v.location + (f":{v.line}" if v.line else "")
                    lines.append(f"         Location : {loc}")

                # ── source snippet ────────────────────────────────────────
                sf = v.source_file
                if sf and v.line:
                    try:
                        from socc.diagnostics import render_source_snippet
                        snippet = render_source_snippet(
                            sf, v.line, color=color, severity=v.severity,
                            hint=v.suggestion.splitlines()[0] if v.suggestion else "",
                        )
                        if snippet:
                            lines.append(snippet)
                    except ImportError:
                        pass

                if v.impact:
                    lines.append(f"         Impact   : {v.impact}")
                if v.suggestion and not (v.source_file and v.line):
                    # Only print Fix separately when there is no snippet
                    # (snippet already shows the hint inline)
                    lines.append(f"         Fix      : {v.suggestion}")
                lines.append("")

        _section(f"\nErrors ({len(errors)}):", errors, "red")
        _section(f"\nWarnings ({len(warnings)}):", warnings, "yellow")
        _section(f"\nInfo ({len(infos)}):", infos, "cyan")

        lines.append("=" * 80)

        # ── per-subsystem breakdown ───────────────────────────────────────────
        domain_counts: Dict[str, int] = defaultdict(int)
        for v in violations:
            domain_counts[_rule_domain(v.code)] += 1
        if domain_counts:
            domain_parts = "  ".join(
                f"{d}:{n}"
                for d, n in sorted(domain_counts.items(), key=lambda x: -x[1])
            )
            breakdown = self._styled(f"[{domain_parts}]", color, fg="white", bold=False)
            lines.append(f"\n  Subsystems: {breakdown}")

        err_str  = self._styled(f"{len(errors)} error(s)",   color, fg="red",    bold=True)
        warn_str = self._styled(f"{len(warnings)} warning(s)", color, fg="yellow")
        info_str = self._styled(f"{len(infos)} info",         color, fg="cyan")
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

    def _generate_annotations_report(self, violations: List[Violation]) -> str:
        """Generate GitHub Actions workflow-command annotations.

        Each line uses the ``::error`` / ``::warning`` / ``::notice`` syntax so
        that GitHub renders violations directly on the diff of a pull request
        without requiring a SARIF upload step.

        See: https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions
        """
        _level_map = {
            "error":   "error",
            "warning": "warning",
            "info":    "notice",
        }
        output_lines = []
        for v in violations:
            level = _level_map.get(v.severity, "notice")
            # Prefer source_file for the file attribute; fall back to location
            file_attr = v.source_file or v.location or "unknown"
            parts = [f"file={file_attr}"]
            if v.line:
                parts.append(f"line={v.line}")
            parts.append(f"title=[{v.code}] {v.rule_name or 'socc'}")
            attrs = ",".join(parts)
            # Newlines in the message would break the annotation format
            msg = v.message.replace("\n", " ")
            output_lines.append(f"::{level} {attrs}::{msg}")
        return "\n".join(output_lines)
