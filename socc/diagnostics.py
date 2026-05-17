"""Rust-compiler-style source-code diagnostic renderer.

Renders a source snippet around a violation line using only ``click.style``
(zero extra dependencies).  Gracefully no-ops when the source file cannot
be read.

Example output::

    error[BND-001]: Physical hardware bounds violation
      --> boards/rk3588-custom.dts:45:18
       |
    44 |     user-led1 {
    45 |         gpios = <&gpio4 35 GPIO_ACTIVE_HIGH>;
       |                         ~~ pin index out of range
       |
       = hint: gpio4 only has 32 pins (indices 0–31)
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import click

# Severity → (foreground-color, bold)
_SEV_COLOR = {
    "error":   ("red",    True),
    "warning": ("yellow", False),
    "info":    ("cyan",   False),
}


def _s(text: str, color: Optional[bool], **kw) -> str:
    """Apply click.style unless color is explicitly disabled."""
    if color is False:
        return text
    return click.style(text, **kw)


def render_source_snippet(
    source_file: str,
    line: int,
    *,
    col: Optional[int] = None,
    context: int = 2,
    color: Optional[bool] = None,
    severity: str = "error",
    hint: str = "",
) -> str:
    """Return a Rust-style annotated source snippet.

    Args:
        source_file: Path to the DTS source file.
        line:        1-based line number of the violation.
        col:         Optional 1-based column of the problematic token.
        context:     Number of surrounding lines to include.
        color:       True=force ANSI, False=plain text, None=auto.
        severity:    Controls underline colour.
        hint:        Optional hint line appended after the snippet.

    Returns:
        Multi-line string ready to be printed.  Empty string if the file
        cannot be read.
    """
    try:
        src_lines = Path(source_file).read_text(errors="replace").splitlines()
    except OSError:
        return ""

    sev_fg, sev_bold = _SEV_COLOR.get(severity, ("red", True))
    total  = len(src_lines)
    start  = max(0, line - 1 - context)
    end    = min(total, line + context)   # exclusive, so range(start, end)

    gutter = len(str(end))
    sep    = _s("|", color, fg="blue", bold=True)
    buf: List[str] = []

    # ── file:line:col header ─────────────────────────────────────────────────
    arrow = _s("-->", color, fg="blue", bold=True)
    loc   = f"{source_file}:{line}" + (f":{col}" if col else "")
    buf.append(f"  {arrow} {loc}")
    buf.append(f"  {' ' * gutter} {sep}")

    # ── source lines ─────────────────────────────────────────────────────────
    for ln_idx in range(start, end):
        ln_no = ln_idx + 1
        src   = src_lines[ln_idx] if ln_idx < total else ""

        if ln_no == line:
            num_s = _s(str(ln_no).rjust(gutter), color, fg=sev_fg, bold=sev_bold)
            bar   = _s("|", color, fg=sev_fg, bold=True)
            buf.append(f"  {num_s} {bar} {src}")

            # caret / underline on the following gutter line
            stripped = src.lstrip()
            indent   = len(src) - len(stripped)
            if col is not None:
                pad   = col - 1
                caret = _s("^^", color, fg=sev_fg, bold=True)
            else:
                pad   = indent
                width = min(len(stripped.rstrip()), 40) or 1
                caret = _s("~" * width, color, fg=sev_fg, bold=True)
            buf.append(f"  {' ' * gutter} {bar} {' ' * pad}{caret}")
        else:
            buf.append(f"  {str(ln_no).rjust(gutter)} {sep} {src}")

    buf.append(f"  {' ' * gutter} {sep}")

    # ── optional hint ────────────────────────────────────────────────────────
    if hint:
        eq = _s("=", color, fg="blue", bold=True)
        hint_label = _s("hint:", color, fg="blue", bold=True)
        buf.append(f"  {' ' * gutter} {eq} {hint_label} {hint}")

    return "\n".join(buf)


def render_diagnostic_header(
    code: str,
    message: str,
    *,
    color: Optional[bool] = None,
    severity: str = "error",
) -> str:
    """Return the first line of a Rust-style diagnostic.

    Example::
        error[BND-001]: Physical hardware bounds violation
    """
    sev_fg, sev_bold = _SEV_COLOR.get(severity, ("red", True))
    label   = _s(severity, color, fg=sev_fg, bold=sev_bold)
    bracket = _s(f"[{code}]", color, fg=sev_fg, bold=True)
    return f"{label}{bracket}: {message}"
