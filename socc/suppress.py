"""Violation suppression helpers for socc.

Three suppression layers (evaluated in order):

1. **Project-level .socc_ignore file**  —  glob-pattern rules similar to .gitignore
2. **Inline DTS comments**              —  ``/* socc-ignore: BND-001 */`` on the same
                                           line (or the line before) the offending node
3. **CLI --ignore-rule flags / config** —  handled upstream by the check command

Additionally, ``socc-expect`` comments declare *intentional* violations:

    /* socc-expect: PIN-202 -- hardware fly-wire with voltage divider */

If the expected violation fires  → it is silenced (same as socc-ignore).
If the expected violation does NOT fire → an INFO pseudo-violation is injected
to remind the engineer to remove the now-stale comment.

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

# ── inline comment patterns ───────────────────────────────────────────────────
# Matches:  /* socc-ignore: BND-001 */
#           // socc-ignore: BND-001, GP-002
#           /* socc-ignore: BND-001 — reason text */
_INLINE_PATTERN = re.compile(
    r'socc-ignore\s*:\s*([\w, -]+)',
    re.IGNORECASE,
)

# Matches:  /* socc-expect: PIN-202 */
#           /* socc-expect: PIN-202, PD-001 -- fly-wire, confirmed */
_EXPECT_PATTERN = re.compile(
    r'socc-expect\s*:\s*([\w, -]+)',
    re.IGNORECASE,
)

# Only tokens that look like a violation code (e.g. MM-006, PIN-301)
# This prevents free-text after "--" from being mistaken for codes.
_CODE_TOKEN_RE = re.compile(r'^[A-Za-z]{1,12}-\d{1,6}$')


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
        """Return violations after removing suppressed entries.

        Also appends INFO pseudo-violations for any ``socc-expect`` comments
        whose expected violation did NOT occur (stale expect).
        """
        inline_map  = _build_inline_map(source_text) if source_text else {}
        expect_map  = _build_expect_map(source_text) if source_text else {}

        kept = []
        satisfied_expects: set = set()   # (line, code) pairs that were matched

        # Fill in missing line numbers by scanning source text for node names.
        if source_text:
            _fill_missing_lines(violations, source_text)

        for v in violations:
            if self._suppressed_by_file(v):
                continue
            if _suppressed_by_inline(v, inline_map):
                continue
            if _suppressed_by_expect(v, expect_map, satisfied_expects):
                continue
            kept.append(v)

        # Inject stale-expect warnings for expects that never fired
        stale = _stale_expect_violations(expect_map, satisfied_expects, source_text)
        kept.extend(stale)

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
            codes = {c.upper() for c in raw_codes if _CODE_TOKEN_RE.match(c)}
            if not codes:
                continue
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


# ── socc-expect helpers ───────────────────────────────────────────────────────

def _build_expect_map(source_text: str) -> dict:
    """Build {line_number: set_of_expected_codes} from ``socc-expect:`` comments.

    Like socc-ignore, an expect comment covers the same line and the next.
    Tokens are validated against the violation-code pattern (e.g. MM-006) so
    that free-text after ``--`` is never misidentified as a code.
    """
    result: dict = {}
    for i, line in enumerate(source_text.splitlines(), start=1):
        m = _EXPECT_PATTERN.search(line)
        if m:
            raw_codes = re.split(r'[\s,]+', m.group(1).strip())
            codes = {c.upper() for c in raw_codes if _CODE_TOKEN_RE.match(c)}
            if not codes:
                continue
            result[i]     = codes
            result[i + 1] = codes
    return result


def _fill_missing_lines(violations: list, source_text: str) -> None:
    """Assign ``line`` to violations that have ``line=None``.

    Searches *source_text* for the node name extracted from each violation's
    ``location`` field (e.g. ``/i2c@fe2b0000`` → look for ``i2c@fe2b0000 {``).
    Mutates violation objects in-place; has no effect if a line is already set.
    """
    _NODE_DECL = re.compile(
        r'^\s*([A-Za-z0-9_,+/@-]+@[0-9a-fA-F]+(?:,[0-9a-fA-F]+)*)\s*\{'
    )
    node_lines: dict = {}
    for idx, ln in enumerate(source_text.splitlines(), start=1):
        m = _NODE_DECL.match(ln)
        if m:
            node_lines[m.group(1)] = idx

    for v in violations:
        if v.line is not None:
            continue
        loc = (v.location or "").rstrip("/")
        node_name = loc.split("/")[-1]
        if node_name and node_name in node_lines:
            v.line = node_lines[node_name]


def _suppressed_by_expect(
    violation, expect_map: dict, satisfied: set
) -> bool:
    """Return True if the violation is covered by a socc-expect comment.

    Side-effect: records ``(line, code)`` into *satisfied* so that
    :func:`_stale_expect_violations` can detect which expects fired.
    """
    if not expect_map or violation.line is None:
        return False
    expected_codes = expect_map.get(violation.line, set())
    code = (violation.code or "").upper()
    if code in expected_codes or "*" in expected_codes:
        satisfied.add((violation.line, code))
        return True
    return False


def _stale_expect_violations(
    expect_map: dict, satisfied: set, source_text: Optional[str]
) -> list:
    """Return INFO pseudo-violations for socc-expect comments that never fired.

    An expect comment is *stale* when no actual violation with the expected
    code appeared near the annotated line.
    """
    from socc.model.base import Violation  # local import to avoid circularity

    if not expect_map:
        return []

    stale = []
    lines = (source_text or "").splitlines()

    # expect_map has duplicates (line i and line i+1 both point to same codes).
    # Collect only the *originating* lines (those that actually have a comment).
    seen_lines: set = set()
    for line_no, codes in expect_map.items():
        # The comment appears on the lower line number of each pair
        origin = line_no if _has_expect_comment(lines, line_no) else line_no - 1
        if origin in seen_lines:
            continue
        seen_lines.add(origin)
        for code in codes:
            if (
                (line_no, code) not in satisfied
                and (line_no - 1, code) not in satisfied
                and (line_no + 1, code) not in satisfied
            ):
                # Try to get surrounding context
                ctx_line = lines[origin - 1] if 0 < origin <= len(lines) else ""
                stale.append(Violation(
                    code="SE-001",
                    severity="info",
                    rule_name="Stale socc-expect Comment",
                    message=(
                        f"Expected violation {code!r} did not occur near line {origin}. "
                        f"The fly-wire or workaround may have been removed."
                    ),
                    impact=(
                        "The socc-expect comment is now dead code and may mask "
                        "future accidental violations of the same rule."
                    ),
                    suggestion=(
                        f"Remove or update the '/* socc-expect: {code} */' "
                        f"comment on line {origin}."
                    ),
                    location=f"<source>:{origin}",
                    line=origin,
                ))
    return stale


def _has_expect_comment(lines: list, line_no: int) -> bool:
    """Return True if *line_no* (1-based) contains a socc-expect comment."""
    if line_no < 1 or line_no > len(lines):
        return False
    return bool(_EXPECT_PATTERN.search(lines[line_no - 1]))
