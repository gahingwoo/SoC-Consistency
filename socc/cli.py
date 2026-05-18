"""Command-line interface — entry point only.

All command logic lives in socc/commands/*.  This module assembles the
click group hierarchy and registers backward-compatible flat aliases so
existing scripts continue to work unchanged.
"""

import click

from socc import __version__

# ── top-level commands ────────────────────────────────────────────────────────
from socc.commands.core import (
    check, rules, version, fix, autofix, diff, smart_diff, explain,
    init_cmd, self_update, bootstrap, install_hook, decompile,
)

# ── command groups ────────────────────────────────────────────────────────────
from socc.commands.audit_cmds    import audit_group
from socc.commands.analyze_cmds  import analyze_group
from socc.commands.generate_cmds import generate_group
from socc.commands.viz_cmds      import viz_group
from socc.commands.sim_cmds      import sim_group
from socc.commands.socdef_cmds   import socdef_group


# ── main group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version=__version__, prog_name="socc")
def cli():
    """socc — SoC device-tree consistency checker.

    \b
    Quick start:
        socc check board.dts --soc rk3588
        socc check board.dts --demo
        socc --help

    \b
    Command groups (new in 1.1):
        audit     DTS bindings, BOM, kernel config, AMP, matrix
        analyze   Memory map, dependency graph, bounds, IRQ, GC
        generate  QEMU scripts, C headers, diagrams, reports
        viz       Topology graph, BGA pinmap, power sequence
        sim       FMEA, smoke, shell, live-check, trace, migrate
        socdef    Vendor .socdef constraint files
    """


# ── register top-level commands ───────────────────────────────────────────────

cli.add_command(check)
cli.add_command(rules)
cli.add_command(version)
cli.add_command(fix)
cli.add_command(autofix)
cli.add_command(diff)
cli.add_command(smart_diff)
cli.add_command(explain)
cli.add_command(init_cmd,     name="init")
cli.add_command(self_update)
cli.add_command(bootstrap)
cli.add_command(install_hook, name="install-hook")
cli.add_command(decompile)

# ── register command groups ───────────────────────────────────────────────────

cli.add_command(audit_group)
cli.add_command(analyze_group)
cli.add_command(generate_group)
cli.add_command(viz_group)
cli.add_command(sim_group)
cli.add_command(socdef_group)


# ── backward-compatible flat aliases (hidden) ─────────────────────────────────

def _alias(group, old_name: str, cmd_name: str):
    cmd = group.commands[cmd_name]
    aliased = click.command(old_name, hidden=True, help=cmd.help)(cmd.callback)
    aliased.params = cmd.params
    cli.add_command(aliased, name=old_name)


# Note: "audit" cannot be aliased — it conflicts with the audit group name.
# Old: socc audit board.dts  → New: socc audit bindings board.dts
_alias(audit_group,    "audit-bom",      "bom")
_alias(audit_group,    "audit-kernel",   "kernel")
_alias(audit_group,    "amp-audit",      "amp")
_alias(audit_group,    "matrix-audit",   "matrix")
_alias(audit_group,    "cross-check",    "cross-check")
_alias(audit_group,    "overlay-check",  "overlay")
_alias(audit_group,    "crosscheck",     "netlist")

_alias(analyze_group,  "check-memory",   "memory")
_alias(analyze_group,  "check-deps",     "deps")
_alias(analyze_group,  "check-bounds",   "bounds")
_alias(analyze_group,  "check-irq",      "irq")
_alias(analyze_group,  "gc",             "gc")

_alias(generate_group, "generate-qemu",       "qemu")
_alias(generate_group, "generate-tests",      "tests")
_alias(generate_group, "generate-saleae",     "saleae")
_alias(generate_group, "export-headers",      "headers")
_alias(generate_group, "generate-diagram",    "diagram")
_alias(generate_group, "generate-compliance", "compliance")
_alias(generate_group, "generate-report",     "report")

_alias(viz_group,      "topology",   "topology")
_alias(viz_group,      "pinmap",     "pinmap")
_alias(viz_group,      "power-seq",  "power-seq")

_alias(sim_group,      "simulate-smoke",  "smoke")
_alias(sim_group,      "shell",           "shell")
_alias(sim_group,      "live-check",      "live-check")
_alias(sim_group,      "live-probe",      "live-probe")
_alias(sim_group,      "trace",           "trace")
_alias(sim_group,      "migrate",         "migrate")

_alias(socdef_group,   "validate-socdef", "validate")
_alias(socdef_group,   "check-socdef",    "check")
_alias(socdef_group,   "init-socdef",     "init")


@cli.command("simulate", hidden=True)
@click.argument("action", type=click.Choice(["failure"]))
@click.argument("node_name")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", default=None)
@click.option("--color/--no-color", default=None)
def _simulate_alias(action, node_name, dts_file, soc, color):
    """[Deprecated] Use 'socc sim failure' instead."""
    from socc.commands._shared import auto_detect_soc, parse_dts_file, Path
    from socc.fmea import simulate_failure, render_fmea_report
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    report = simulate_failure(model, node_name)
    click.echo(render_fmea_report(report, use_color=color))
    if report.severity in ("FATAL", "CRITICAL"):
        raise SystemExit(1)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    """CLI main entry point."""
    cli()


if __name__ == "__main__":
    main()
