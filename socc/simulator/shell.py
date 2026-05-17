"""Interactive power/clock state machine simulator.

Provides a REPL (Read-Eval-Print Loop) where the user can simulate power
rail and clock state transitions on a parsed SoC model and observe cascading
effects in real time.

Commands
────────
  status              Show current rail/clock states (enabled / disabled)
  tree                Print the power tree with current on/off state
  turn_off <rail>     Simulate powering off a supply rail + cascade
  turn_on  <rail>     Simulate powering on a supply rail + cascade
  affected <rail>     Show what would be lost if <rail> is turned off
  check               Re-run consistency rules on the current enabled state
  help                Show available commands
  quit / exit / q     Exit the simulator

Cascade rules
─────────────
When a rail is turned OFF:
  1. All child rails in the power tree are also turned OFF (recursively).
  2. All devices that list this rail (or any child) as their supply are
     flagged as ``POWERED-OFF``.
  3. Clock signals whose provider device is now powered off are also
     flagged as ``STOPPED``.

When a rail is turned ON:
  The rail itself transitions to ON but child rails remain in their current
  state (they each have their own enable logic).
"""

from __future__ import annotations

import shlex
from typing import Dict, List, Optional, Set

try:
    import click
    _HAS_CLICK = True
except ImportError:
    _HAS_CLICK = False


# ─────────────────────────────────────────────────────────────────────────────
# Color helpers (degrade gracefully without click)
# ─────────────────────────────────────────────────────────────────────────────

def _c(text: str, fg: str = "", bold: bool = False) -> str:
    if not _HAS_CLICK:
        return text
    return click.style(text, fg=fg or None, bold=bold)


def _ok(text: str) -> str:
    return _c(text, "green")


def _warn(text: str) -> str:
    return _c(text, "yellow")


def _err(text: str) -> str:
    return _c(text, "red", bold=True)


def _info(text: str) -> str:
    return _c(text, "cyan")


def _bold(text: str) -> str:
    return _c(text, bold=True)


# ─────────────────────────────────────────────────────────────────────────────
# Simulator
# ─────────────────────────────────────────────────────────────────────────────


class PowerSimulator:
    """Interactive power/clock state machine for a SoC model."""

    def __init__(self, model, soc_name: str = "unknown"):
        from socc.model import SoC
        self.model = model
        self.soc_name = soc_name

        # Initial state: all rails ON, all devices ENABLED
        self.rail_state: Dict[str, bool] = {
            name: True for name in model.power_tree.nodes
        }
        self.device_state: Dict[str, bool] = {
            name: True for name in model.devices
        }
        self.clock_state: Dict[str, bool] = {
            name: True for name in model.clock_tree.clocks
        }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _cascade_off(self, rail: str) -> Dict[str, List[str]]:
        """Turn off *rail* and compute cascading effects.

        Returns a dict with keys:
          'rails'   — list of rails turned off
          'devices' — list of devices now without power
          'clocks'  — list of clocks that stopped
        """
        # BFS to collect all downstream rails
        turned_off_rails: List[str] = []
        queue = [rail]
        visited: Set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current in self.rail_state:
                self.rail_state[current] = False
                turned_off_rails.append(current)
            # add children
            for child in self.model.power_tree.edges.get(current, []):
                queue.append(child)

        # Devices powered by any of the turned-off rails
        off_set = set(turned_off_rails)
        affected_devices: List[str] = []
        for dev_name, supplies in self.model.device_supplies.items():
            if any(s in off_set for s in supplies):
                if self.device_state.get(dev_name, True):
                    self.device_state[dev_name] = False
                    affected_devices.append(dev_name)

        # Clocks whose consumer devices are now all offline
        stopped_clocks: List[str] = []
        off_devices = set(affected_devices)
        for clk_name, clk in self.model.clock_tree.clocks.items():
            if not self.clock_state.get(clk_name, True):
                continue  # already off
            consumers = set(clk.consumers)
            if consumers and consumers.issubset(off_devices):
                self.clock_state[clk_name] = False
                stopped_clocks.append(clk_name)

        return {
            "rails": turned_off_rails,
            "devices": affected_devices,
            "clocks": stopped_clocks,
        }

    def _cascade_on(self, rail: str) -> None:
        """Turn on a single rail (no auto-cascade upward)."""
        self.rail_state[rail] = True
        # Re-enable devices that depend ONLY on now-on rails
        for dev_name, supplies in self.model.device_supplies.items():
            if all(self.rail_state.get(s, False) for s in supplies):
                self.device_state[dev_name] = True

    def _all_affected_by_off(self, rail: str) -> Dict[str, List[str]]:
        """Preview what would be affected if *rail* were turned off (no state change)."""
        # Temporary simulation without mutating state
        temp_rails = dict(self.rail_state)
        temp_devices = dict(self.device_state)
        temp_clocks = dict(self.clock_state)

        queue = [rail]
        visited: Set[str] = set()
        pred_rails: List[str] = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            if current in temp_rails:
                pred_rails.append(current)
            for child in self.model.power_tree.edges.get(current, []):
                queue.append(child)

        off_set = set(pred_rails)
        pred_devices: List[str] = []
        for dev_name, supplies in self.model.device_supplies.items():
            if any(s in off_set for s in supplies):
                pred_devices.append(dev_name)

        off_dev_set = set(pred_devices)
        pred_clocks: List[str] = []
        for clk_name, clk in self.model.clock_tree.clocks.items():
            consumers = set(clk.consumers)
            if consumers and consumers.issubset(off_dev_set):
                pred_clocks.append(clk_name)

        return {"rails": pred_rails, "devices": pred_devices, "clocks": pred_clocks}

    # ── Display helpers ───────────────────────────────────────────────────

    def _print(self, msg: str) -> None:
        print(msg)

    def _show_status(self) -> None:
        on_rails = sorted(r for r, s in self.rail_state.items() if s)
        off_rails = sorted(r for r, s in self.rail_state.items() if not s)
        on_devs = sorted(d for d, s in self.device_state.items() if s)
        off_devs = sorted(d for d, s in self.device_state.items() if not s)

        self._print(_bold("\n── Power Rail Status ──────────────────────────"))
        for r in on_rails:
            reg = self.model.power_tree.nodes[r]
            v = f"{reg.voltage_min:.1f}–{reg.voltage_max:.1f}V"
            self._print(f"  {_ok('ON ')}  {r:<28} {v}")
        for r in off_rails:
            self._print(f"  {_err('OFF')}  {_c(r, 'red')}")

        self._print(_bold("\n── Device Status ──────────────────────────────"))
        for d in on_devs:
            self._print(f"  {_ok('ON ')}  {d}")
        for d in off_devs:
            self._print(f"  {_err('OFF')}  {_c(d, 'red')}")

        self._print("")

    def _show_tree(self) -> None:
        """Print power tree with current state indicators."""
        self._print(_bold("\n── Power Tree ─────────────────────────────────"))
        # Print roots first
        roots = [
            r for r in self.model.power_tree.nodes
            if not self.model.power_tree.reverse_edges.get(r)
        ]
        visited: Set[str] = set()

        def _print_node(name: str, indent: int) -> None:
            if name in visited:
                return
            visited.add(name)
            state = _ok("▶ ON ") if self.rail_state.get(name, True) else _err("■ OFF")
            reg = self.model.power_tree.nodes.get(name)
            v = f"{reg.voltage_min:.1f}V" if reg else ""
            prefix = "  " * indent + ("└─ " if indent else "")
            self._print(f"  {prefix}{state} {name:<28} {v}")
            for child in self.model.power_tree.edges.get(name, []):
                _print_node(child, indent + 1)

        for root in sorted(roots):
            _print_node(root, 0)
        self._print("")

    # ── Command handlers ──────────────────────────────────────────────────

    def _cmd_turn_off(self, args: List[str]) -> None:
        if not args:
            self._print(_warn("Usage: turn_off <rail_name>"))
            return
        rail = args[0]
        if rail not in self.rail_state:
            self._print(_err(f"Unknown rail: '{rail}'"))
            self._print("  Available rails: " + ", ".join(sorted(self.rail_state)))
            return
        if not self.rail_state[rail]:
            self._print(_warn(f"Rail '{rail}' is already OFF."))
            return

        result = self._cascade_off(rail)

        self._print(_bold(f"\nSimulating: turn_off {rail}"))
        self._print("")

        rails_str = ", ".join(result["rails"])
        self._print(_err(f"  ■ Rails turned OFF   : {rails_str}"))

        if result["devices"]:
            devs_str = ", ".join(result["devices"])
            self._print(_err(f"  ■ Devices lost power : {devs_str}"))
        else:
            self._print(_ok("  ✓ No devices lost power."))

        if result["clocks"]:
            clks_str = ", ".join(result["clocks"])
            self._print(_warn(f"  ⚠ Clocks stopped    : {clks_str}"))

        # Warn about critical devices (uart / mmc)
        critical = [
            d for d in result["devices"]
            if any(kw in d.lower() for kw in ["uart", "serial", "emmc", "mmc"])
        ]
        if critical:
            self._print("")
            self._print(_err(
                f"  ⚠ WARNING: Critical device(s) lost power: "
                + ", ".join(critical)
                + ".  System may hang!"
            ))

        self._print("")

    def _cmd_turn_on(self, args: List[str]) -> None:
        if not args:
            self._print(_warn("Usage: turn_on <rail_name>"))
            return
        rail = args[0]
        if rail not in self.rail_state:
            self._print(_err(f"Unknown rail: '{rail}'"))
            return
        if self.rail_state[rail]:
            self._print(_warn(f"Rail '{rail}' is already ON."))
            return

        self._cascade_on(rail)
        self._print(_ok(f"\n  ▶ Rail '{rail}' turned ON."))
        # re-enabled devices
        re_enabled = [
            d for d, s in self.device_state.items()
            if s and any(
                r in (self.model.device_supplies.get(d) or [])
                for r in [rail]
            )
        ]
        if re_enabled:
            self._print(_ok("  ▶ Devices re-enabled : " + ", ".join(re_enabled)))
        self._print("")

    def _cmd_affected(self, args: List[str]) -> None:
        if not args:
            self._print(_warn("Usage: affected <rail_name>"))
            return
        rail = args[0]
        if rail not in self.model.power_tree.nodes:
            self._print(_err(f"Unknown rail: '{rail}'"))
            return
        result = self._all_affected_by_off(rail)
        self._print(_bold(f"\nIf '{rail}' is turned OFF:"))
        self._print(_err("  Rails that go down : " + (", ".join(result["rails"]) or "none")))
        self._print(_err("  Devices lose power : " + (", ".join(result["devices"]) or "none")))
        if result["clocks"]:
            self._print(_warn("  Clocks that stop   : " + ", ".join(result["clocks"])))
        self._print("")

    def _cmd_check(self) -> None:
        """Re-run the rule checker on the current model state."""
        try:
            from socc.rules import RuleRegistry
            from socc.engine import Checker
            from socc.rules.common import register_common_rules
            registry = RuleRegistry()
            register_common_rules(registry)
            checker = Checker(registry)
            violations = checker.check(self.model, self.soc_name)
            if violations:
                self._print(_bold(f"\n  {len(violations)} violation(s) found:"))
                for v in violations:
                    tag = _err("[E]") if v.severity == "error" else _warn("[W]")
                    self._print(f"  {tag} [{v.code}] {v.message[:100]}")
            else:
                self._print(_ok("\n  ✓ No violations found."))
        except Exception as e:
            self._print(_err(f"  Checker error: {e}"))
        self._print("")

    def _cmd_help(self) -> None:
        self._print(_bold("\n── socc shell — Available Commands ────────────"))
        cmds = [
            ("status",           "Show current power rail and device states"),
            ("tree",             "Print power tree topology with on/off state"),
            ("turn_off <rail>",  "Simulate powering off a rail (cascade)"),
            ("turn_on  <rail>",  "Simulate powering on a rail"),
            ("affected <rail>",  "Preview impact of turning off a rail"),
            ("check",            "Re-run consistency rules on current model"),
            ("help",             "Show this help message"),
            ("quit / exit / q",  "Exit the simulator"),
        ]
        for cmd, desc in cmds:
            self._print(f"  {_info(cmd):<32} {desc}")
        self._print("")

    # ── REPL ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Start the interactive REPL."""
        banner = _bold(f"""
╔══════════════════════════════════════════════════════════════╗
║      socc interactive power simulator  —  {self.soc_name:<18}  ║
╚══════════════════════════════════════════════════════════════╝

  {_ok(str(len(self.rail_state)))} rails  ·  {_ok(str(len(self.device_state)))} devices  ·  {_ok(str(len(self.clock_state)))} clocks loaded.
  Type 'help' for available commands.  Ctrl-C or 'quit' to exit.
""")
        print(banner)

        while True:
            try:
                raw = input(_bold(f"[socc:{self.soc_name}]> "))
            except (KeyboardInterrupt, EOFError):
                print("\nExiting simulator.")
                break

            raw = raw.strip()
            if not raw:
                continue

            try:
                parts = shlex.split(raw)
            except ValueError as e:
                self._print(_err(f"Parse error: {e}"))
                continue

            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in {"quit", "exit", "q"}:
                print("Exiting simulator.")
                break
            elif cmd == "help":
                self._cmd_help()
            elif cmd == "status":
                self._show_status()
            elif cmd == "tree":
                self._show_tree()
            elif cmd == "turn_off":
                self._cmd_turn_off(args)
            elif cmd == "turn_on":
                self._cmd_turn_on(args)
            elif cmd == "affected":
                self._cmd_affected(args)
            elif cmd == "check":
                self._cmd_check()
            else:
                self._print(_warn(f"Unknown command: '{cmd}'.  Type 'help' for usage."))


__all__ = ["PowerSimulator"]
