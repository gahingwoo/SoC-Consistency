"""Text and JSON renderers for simulation results."""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from socc.simulation.types import ScenarioResult, SimViolation


# ANSI colour codes (used when use_color=True)
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


def _c(text: str, code: str, use_color: bool) -> str:
    return f"{code}{text}{_RESET}" if use_color else text


# ── Severity colour helper ────────────────────────────────────────────────────

def _severity_color(severity: str, use_color: bool) -> str:
    if not use_color:
        return severity
    colors = {"error": _RED, "warning": _YELLOW, "info": _CYAN}
    return _c(severity, colors.get(severity, ""), use_color)


# ── Public renderers ──────────────────────────────────────────────────────────

def render_text(
    results: Dict[str, ScenarioResult],
    *,
    min_severity: str = "warning",
    show_timeline: bool = False,
    use_color: Optional[bool] = None,
) -> str:
    """Render simulation results as a human-readable text report."""
    if use_color is None:
        import sys
        use_color = sys.stdout.isatty()

    _sev_rank = {"error": 0, "warning": 1, "info": 2}
    min_rank = _sev_rank.get(min_severity, 1)

    lines: List[str] = []

    for scenario, result in results.items():
        hdr = f"  Scenario: {scenario}"
        lines.append(_c(hdr, _BOLD, use_color))

        filtered = [
            v for v in result.violations
            if _sev_rank.get(v.severity, 99) <= min_rank
        ]

        if show_timeline and result.timeline:
            lines.append(_c("  Timeline:", _DIM, use_color))
            for ev in result.timeline:
                line = (
                    f"    t={ev.time_ms:8.3f}ms  "
                    f"[{ev.component_type:10s}] "
                    f"{ev.component}: "
                    f"{ev.old_state} → {ev.new_state}"
                )
                if ev.triggered_by:
                    line += f"  (← {ev.triggered_by})"
                lines.append(_c(line, _DIM, use_color))

        if not filtered:
            lines.append(
                _c(f"    No {min_severity}-level violations found.", _GREEN, use_color)
            )
        else:
            for v in filtered:
                tag = _c(f"  {v.severity}[{v.code}]", _RED if v.severity == "error" else _YELLOW, use_color)
                lines.append(f"{tag}  {v.message}")
                lines.append(f"    {_c('Detail:', _DIM, use_color)}  {v.detail}")
                lines.append(f"    {_c('Fix:   ', _DIM, use_color)}  {v.suggestion}")

        # Summary line
        safe_str = _c("SAFE", _GREEN, use_color) if result.is_safe else _c("UNSAFE", _RED, use_color)
        lines.append(
            f"  [{safe_str}] "
            f"{result.error_count} error(s), {result.warning_count} warning(s)  "
            f"duration={result.duration_ms:.1f}ms"
        )
        lines.append("")

    return "\n".join(lines)


def render_json(results: Dict[str, ScenarioResult]) -> str:
    """Render simulation results as compact JSON."""
    payload: Dict = {}
    for scenario, result in results.items():
        payload[scenario] = {
            "is_safe": result.is_safe,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "duration_ms": result.duration_ms,
            "violations": [
                {
                    "code": v.code,
                    "severity": v.severity,
                    "message": v.message,
                    "time_ms": v.time_ms,
                    "component": v.component,
                    "detail": v.detail,
                    "suggestion": v.suggestion,
                }
                for v in result.violations
            ],
        }
    return json.dumps(payload, indent=2)
