"""Constraint loader for the simulation layer.

Reads the *simulation_constraints* section from a SoC YAML file and returns
the structured constraint dicts expected by the state machines.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


# ── Public API ────────────────────────────────────────────────────────────────

def load_sim_constraints(soc_yaml_path: str) -> Dict[str, Any]:
    """Load and return the simulation_constraints block from a SoC YAML file.

    If the file does not exist or has no simulation_constraints section an
    empty dict is returned — all state machines will fall back to permissive
    / zero-delay defaults.

    Returns a dict with keys:
        power_sequencing    (list of dicts)
        clock_gating        (list of dicts)
        reset_dependencies  (list of dicts)
        required_resets_patterns (list of dicts)
    """
    path = Path(soc_yaml_path)
    if not path.exists():
        return _empty_constraints()

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    block = data.get("simulation_constraints", {})
    if not block:
        return _empty_constraints()

    return {
        "power_sequencing": block.get("power_sequencing", []),
        "clock_gating": block.get("clock_gating", []),
        "reset_dependencies": block.get("reset_dependencies", []),
        "required_resets_patterns": block.get("required_resets_patterns", []),
    }


def stability_requirements_from_constraints(
    constraints: Dict[str, Any],
) -> Dict[str, float]:
    """Build a stability_requirements dict suitable for PowerStateMachine.

    Returns: {regulator_name: required_stable_ms, ...}
    """
    result: Dict[str, float] = {}
    for entry in constraints.get("power_sequencing", []):
        name = entry.get("regulator")
        ms = entry.get("stable_before_consumers_ms", 0.0)
        if name and ms:
            result[name] = float(ms)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _empty_constraints() -> Dict[str, Any]:
    return {
        "power_sequencing": [],
        "clock_gating": [],
        "reset_dependencies": [],
        "required_resets_patterns": [],
    }


def find_soc_yaml(soc_name: str) -> Optional[str]:
    """Locate the YAML file for *soc_name* within the package data directory.

    Checks the package-bundled ``socc/data/soc/`` tree first (works for wheel
    installs), then falls back to ``data/soc/`` relative to the project root
    (works for editable/development installs).
    """
    # Validate soc_name to prevent path traversal (OWASP A01)
    if not soc_name or not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', soc_name):
        return None

    # Primary: inside the socc package (included in wheels via package-data)
    pkg_root = Path(__file__).resolve().parent.parent  # .../socc/
    pkg_candidates = [
        pkg_root / "data" / "soc" / "rockchip" / f"{soc_name}.yaml",
        pkg_root / "data" / "soc" / f"{soc_name}.yaml",
    ]
    for p in pkg_candidates:
        if p.exists():
            return str(p)

    # Fallback: project-root data/ (editable installs, development)
    dev_root = pkg_root.parent
    dev_candidates = [
        dev_root / "data" / "soc" / "rockchip" / f"{soc_name}.yaml",
        dev_root / "data" / "soc" / f"{soc_name}.yaml",
    ]
    for p in dev_candidates:
        if p.exists():
            return str(p)

    return None
