"""DTS preprocess bridge.

Detects when a DTS file still contains CPP directives or has been left as a
compiled DTB, and optionally invokes an external preprocessor/decompiler so
that socc's parser receives clean, expanded DTS text.

Usage model
-----------
* Detection only (always-on):
    ``detect_unpreprocessed(content, path)`` returns a human-readable
    diagnostic string when CPP tokens or known macros are found.  Returns
    ``None`` for clean input.

* Auto-preprocess (``--preprocess`` flag / ``preprocess=True``):
    ``preprocess_file(path)`` dispatches to the correct external tool:
    - DTB binary  →  ``dtc -I dtb -O dts``
    - DTS/DTSI    →  ``cpp -x assembler-with-cpp -P``
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


# ── Exception ─────────────────────────────────────────────────────────────────

class UnpreprocessedDTSError(ValueError):
    """Raised when a DTS file contains unprocessed CPP directives or macros,
    or when a binary DTB is passed without ``--preprocess``."""


# ── CPP detection patterns ────────────────────────────────────────────────────

# Patterns checked against comment-stripped content.
# Each entry is (compiled-regex, human-readable label).
_CPP_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*#\s*include\b",  re.MULTILINE), "#include directive"),
    (re.compile(r"^\s*#\s*define\b",   re.MULTILINE), "#define directive"),
    (re.compile(r"^\s*#\s*ifdef\b",    re.MULTILINE), "#ifdef directive"),
    (re.compile(r"^\s*#\s*ifndef\b",   re.MULTILINE), "#ifndef directive"),
    (re.compile(r"^\s*#\s*if\b",       re.MULTILINE), "#if directive"),
    (re.compile(r"^\s*#\s*endif\b",    re.MULTILINE), "#endif directive"),
    (re.compile(r"^\s*#\s*undef\b",    re.MULTILINE), "#undef directive"),
    (re.compile(r"/include/\s*\"",     re.MULTILINE), "/include/ DTS directive"),
]

# Well-known dt-bindings macro names that must have been expanded by CPP.
# Seeing them in raw DTS means CPP was never run.
_MACRO_RE = re.compile(
    r"\b("
    r"IRQ_TYPE_(?:LEVEL|EDGE)_\w+"
    r"|GPIO_ACTIVE_(?:HIGH|LOW)"
    r"|REGULATOR_MODE_\w+"
    r"|CLK_(?:SET_RATE_PARENT|IGNORE_UNUSED|IS_CRITICAL)"
    r"|GIC_(?:SPI|PPI)"
    r"|DT_[A-Z_]{3,}"
    r")\b"
)


def detect_unpreprocessed(content: str, path: str = "") -> Optional[str]:
    """Return a diagnostic message if *content* appears to contain CPP tokens.

    Strips ``/* */`` and ``//`` comments first so that examples inside
    comments never trigger a false positive.

    Returns ``None`` when the content looks like fully-preprocessed DTS.
    """
    # Strip block and line comments before scanning.
    cleaned = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    cleaned = re.sub(r"//[^\n]*", "", cleaned)

    def _line_of(pos: int) -> int:
        return content[:pos].count("\n") + 1

    for pattern, label in _CPP_PATTERNS:
        m = pattern.search(cleaned)
        if m:
            path_hint = f" in {path!r}" if path else ""
            cpp_cmd = f"cpp -x assembler-with-cpp -P {path or '<file>'}"
            return (
                f"This DTS appears unpreprocessed{path_hint} — "
                f"found {label} at line {_line_of(m.start())}.\n"
                f"Run with --preprocess to let socc invoke cpp automatically, or "
                f"preprocess manually:\n"
                f"  {cpp_cmd} > preprocessed.dts\n"
                f"  socc check preprocessed.dts"
            )

    m = _MACRO_RE.search(cleaned)
    if m:
        path_hint = f" in {path!r}" if path else ""
        return (
            f"This DTS appears unpreprocessed{path_hint} — "
            f"found unexpanded macro {m.group()!r} at line {_line_of(m.start())}.\n"
            f"Run with --preprocess to let socc invoke cpp automatically."
        )

    return None


# ── Binary DTB detection ──────────────────────────────────────────────────────

_DTB_MAGIC = b"\xd0\x0d\xfe\xed"


def is_dtb(path: str) -> bool:
    """Return True if *path* starts with the DTB magic bytes (``0xd00dfeed``)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == _DTB_MAGIC
    except OSError:
        return False


# ── Tool discovery & install hints ───────────────────────────────────────────

def _find_cpp() -> Optional[str]:
    for candidate in ("cpp", "clang", "gcc"):
        exe = shutil.which(candidate)
        if exe:
            return exe
    return None


def _find_dtc() -> Optional[str]:
    return shutil.which("dtc")


_INSTALL_HINTS: dict[str, str] = {
    "cpp": (
        "Install a C preprocessor:\n"
        "  macOS:   xcode-select --install   (or)   brew install llvm\n"
        "  Debian:  sudo apt install cpp\n"
        "  Fedora:  sudo dnf install gcc"
    ),
    "dtc": (
        "Install dtc (Device Tree Compiler):\n"
        "  macOS:   brew install dtc\n"
        "  Debian:  sudo apt install device-tree-compiler\n"
        "  Fedora:  sudo dnf install dtc"
    ),
}


# ── Preprocess dispatcher ────────────────────────────────────────────────────

def preprocess_file(path: str) -> str:
    """Preprocess *path* and return the resulting DTS text.

    Dispatch rules:

    * **DTB binary** (magic ``0xd00dfeed`` or ``.dtb`` extension):
      decompiled via ``dtc -I dtb -O dts``.
    * **DTS / DTSI** (text): run through ``cpp -x assembler-with-cpp -P``.
      The source directory is added to the include search path so that
      relative ``#include`` references resolve correctly.

    Raises :class:`UnpreprocessedDTSError` if the required external tool is
    not installed or exits non-zero.
    """
    if is_dtb(path) or path.lower().endswith(".dtb"):
        return _dtb_to_dts(path)
    return _cpp_preprocess(path)


def _dtb_to_dts(path: str) -> str:
    dtc_exe = _find_dtc()
    if not dtc_exe:
        raise UnpreprocessedDTSError(
            f"Cannot decompile DTB — dtc not found on PATH.\n"
            f"{_INSTALL_HINTS['dtc']}"
        )
    result = subprocess.run(
        [dtc_exe, "-I", "dtb", "-O", "dts", "-o", "-", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise UnpreprocessedDTSError(
            f"dtc failed to decompile {path!r}:\n{result.stderr.strip()}"
        )
    return result.stdout


def _cpp_preprocess(path: str) -> str:
    cpp_exe = _find_cpp()
    if not cpp_exe:
        raise UnpreprocessedDTSError(
            f"Cannot preprocess DTS — no C preprocessor (cpp/clang/gcc) found on PATH.\n"
            f"{_INSTALL_HINTS['cpp']}"
        )
    # Include the source file's directory so relative #include paths work.
    src_dir = str(Path(path).parent)
    result = subprocess.run(
        [cpp_exe, "-x", "assembler-with-cpp", "-P", f"-I{src_dir}", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise UnpreprocessedDTSError(
            f"cpp preprocessing failed for {path!r}:\n{result.stderr.strip()}"
        )
    return result.stdout
