"""Violation suppression helpers for socc.

Three suppression layers (evaluated in order):

1. **Project-level .socc_ignore file**  —  glob-pattern rules similar to .gitignore
2. **Inline DTS comments**              —  ``/* socc-ignore: BND-001 */`` on the same
                                           line (or the line before) the offending node
3. **CLI --ignore-rule flags / config** —  handled upstream by the check command

The public API used by :class:`socc.engine.checker.Checker` is:

    from socc.suppress import SuppressFilter
    sf = SuppressFilter.load(project_root=".")
    clean_violations = sf.apply(violations, dts_source_text)
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import List, Optional, Sequence

# ── inline comment pattern ────────────────────────────────────────────────────
# Matches:  /* socc-ignore: BND-001 */
#           // socc-ignore: BND-001, GP-002
#           /* socc-ignore: BND-001 — reason text */
_INLINE_PATTERN = re.compile(
    r'socc-ignore\s*:\s*([\w, -]+)',
    re.IGNORECASE,
)


class _IgnoreRule:
    """A single line from .socc_ignore."""

    __slots__ = ("code", "path_pattern", "comment")

    def __init__(self, code: str, path_pattern: str = "*", comment: str = ""):
        self.code         = code.strip().upper()
        self.path_pattern = path_pattern.strip()
        self.comment      = comment.strip()

    def matches(self, violation_code: str, location: str) -> bool:
        code_ok = (self.code == "*" or self.code == violation_code.upper())
        if not code_ok:
            return False
        # Glob match against the violation location / path
        if self.path_pattern == "*":
            return True
        loc = (location or "").lstrip("/")
        return fnmatch.fnmatch(loc, self.path_pattern.lstrip("/"))


class SuppressFilter:
    """Combines project-level .socc_ignore rules with inline DTS comments."""

    def __init__(self, rules: List[_IgnoreRule]):
        self._rules = rules

    # ── factories ─────────────────────────────────────────────────────────────

    @classmethod
    def load(cls, project_root: str | Path = ".") -> "SuppressFilter":
        """Load rules from <project_root>/.socc_ignore (if present)."""
        ignore_path = Path(project_root) / ".socc_ignore"
        rules: List[_IgnoreRule] = []
        if ignore_path.exists():
            for raw_line in ignore_path.read_text().splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # Format:  CODE [PATH_GLOB]  [# comment]
                parts = line.split("#", 1)
                comment = parts[1].strip() if len(parts) > 1 else ""
                tokens = parts[0].split()
                if not tokens:
                    continue
                code    = tokens[0]
                pattern = tokens[1] if len(tokens) > 1 else "*"
                rules.append(_IgnoreRule(code, pattern, comment))
        return cls(rules)

    @classmethod
    def empty(cls) -> "SuppressFilter":
        return cls([])

    # ── core API ──────────────────────────────────────────────────────────────

    def apply(
        self,
        violations,                          # List[Violation]
        source_text: Optional[str] = None,   # raw DTS file content (for inline)
    ):
        """Return violations after removing suppressed entries."""
        inline_map = _build_inline_map(source_text) if source_text else {}
        kept = []
        for v in violations:
            if self._suppressed_by_file(v):
                continue
            if _suppressed_by_inline(v, inline_map):
                continue
            kept.append(v)
        return kept

    def _suppressed_by_file(self, violation) -> bool:
        code = (violation.code or "").upper()
        loc  = violation.location or ""
        return any(r.matches(code, loc) for r in self._rules)

    def stats(self) -> dict:
        return {"project_rules": len(self._rules)}


# ── inline suppression helpers ────────────────────────────────────────────────

def _build_inline_map(source_text: str) -> dict:
    """Build {line_number: set_of_suppressed_codes} from DTS source text.

    A ``socc-ignore`` comment suppresses violations *on the same line* or
    *on the immediately following line*.
    """
    result: dict = {}
    for i, line in enumerate(source_text.splitlines(), start=1):
        m = _INLINE_PATTERN.search(line)
        if m:
            raw_codes = re.split(r'[\s,]+', m.group(1).strip())
            codes = {c.upper() for c in raw_codes if c and c not in {"-", "—"}}
            result[i]     = codes        # same line
            result[i + 1] = codes        # next line (node declaration follows)
    return result


def _suppressed_by_inline(violation, inline_map: dict) -> bool:
    """Return True if the violation is covered by an inline socc-ignore comment."""
    if not inline_map or violation.line is None:
        return False
    suppressed_codes = inline_map.get(violation.line, set())
    code = (violation.code or "").upper()
    return code in suppressed_codes or "*" in suppressed_codes
