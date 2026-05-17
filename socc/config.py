"""Config file loader for .socc.yaml."""

from pathlib import Path
from typing import List, Optional
import yaml


DEFAULT_CONFIG = {
    "default_soc": "auto",
    "ignore_rules": [],
    "min_severity": "info",
    "format": "text",
}

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def load_config(cwd: Optional[str] = None) -> dict:
    """Load .socc.yaml from *cwd* (or CWD if None), merging with defaults."""
    search_dir = Path(cwd) if cwd else Path.cwd()
    config_path = search_dir / ".socc.yaml"
    if not config_path.exists():
        # also check parent dirs up to home
        for parent in search_dir.parents:
            candidate = parent / ".socc.yaml"
            if candidate.exists():
                config_path = candidate
                break
            if parent == Path.home():
                break
        else:
            return dict(DEFAULT_CONFIG)

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return dict(DEFAULT_CONFIG)

    config = dict(DEFAULT_CONFIG)
    if "default_soc" in raw:
        config["default_soc"] = str(raw["default_soc"])
    if "ignore_rules" in raw and isinstance(raw["ignore_rules"], list):
        config["ignore_rules"] = [str(r) for r in raw["ignore_rules"]]
    if "min_severity" in raw and raw["min_severity"] in _SEVERITY_ORDER:
        config["min_severity"] = raw["min_severity"]
    if "format" in raw and raw["format"] in ("text", "json", "sarif"):
        config["format"] = raw["format"]
    return config


def filter_by_severity(violations: list, min_severity: str) -> list:
    """Return violations whose severity is >= *min_severity*."""
    threshold = _SEVERITY_ORDER.get(min_severity, 2)
    return [v for v in violations if _SEVERITY_ORDER.get(v.severity, 2) <= threshold]


SAMPLE_CONFIG = """\
# socc configuration file
# https://github.com/your-org/soc-consistency

# Target SoC (rk3588 | rk3566 | rk3399 | auto)
default_soc: auto

# Rules to suppress globally (e.g. informational rules you don't care about)
ignore_rules: []
#  - PD-006   # unused regulator
#  - CK-104   # unused clock provider
#  - GEN-401  # orphaned node

# Minimum severity to report (error | warning | info)
min_severity: info

# Default output format (text | json | sarif)
format: text
"""
