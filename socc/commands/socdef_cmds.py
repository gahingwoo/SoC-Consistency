"""socc socdef <subcommand> — .socdef file commands."""

from __future__ import annotations

import click

from socc.commands._shared import parse_dts_file, Path, Optional


@click.group("socdef")
def socdef_group():
    """Manage vendor .socdef hardware constraint definition files."""


# ── socdef validate ───────────────────────────────────────────────────────────

@socdef_group.command("validate")
@click.argument("socdef_file")
@click.option("--color/--no-color", default=True)
def socdef_validate(socdef_file: str, color: bool):
    """Validate a .socdef hardware constraint definition file."""
    from socc.socdef import parse_socdef, validate_socdef as _validate
    soc_def = parse_socdef(socdef_file)
    errors = _validate(soc_def)
    _C = ("\033[1;31m", "\033[1;33m", "\033[1;32m", "\033[0m") if color else ("", "", "", "")
    if not errors:
        click.echo(f"{_C[2]}[OK] {socdef_file} is valid ({soc_def.soc} v{soc_def.version}){_C[3]}")
    else:
        for e in errors:
            col = _C[0] if e.severity == "error" else _C[1]
            click.echo(f"{col}[{e.severity.upper()}] {e.field}: {e.message}{_C[3]}")
        raise SystemExit(1)


# ── socdef check ──────────────────────────────────────────────────────────────

@socdef_group.command("check")
@click.argument("dts_file")
@click.option("--socdef", required=True, help=".socdef constraint file to apply")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def socdef_check(dts_file: str, socdef: str, color: bool, output: Optional[str]):
    """Check a DTS file against a vendor .socdef constraint file."""
    from socc.socdef import parse_socdef, check_dts_against_socdef, render_socdef_violations
    soc_def = parse_socdef(socdef)
    soc_model = parse_dts_file(dts_file)
    violations = check_dts_against_socdef(soc_model, soc_def)
    text = render_socdef_violations(violations, soc_def, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Written to {output}")
    else:
        click.echo(text)
    if sum(1 for v in violations if v.severity in ("FATAL", "CRITICAL")) > 0:
        raise SystemExit(1)


# ── socdef init ───────────────────────────────────────────────────────────────

@socdef_group.command("init")
@click.option("--soc", required=True, help="SoC name (e.g. rk3588, imx8mp)")
@click.option("--vendor", default="unknown", show_default=True)
@click.option("-o", "--output", default=None)
def socdef_init(soc: str, vendor: str, output: Optional[str]):
    """Generate a .socdef template file for a new SoC.

    \b
    Example:
        socc socdef init --soc rk3588 --vendor rockchip
    """
    from socc.socdef import generate_socdef_template
    tmpl = generate_socdef_template(soc, vendor)
    out = output or f"{soc}.socdef"
    Path(out).write_text(tmpl)
    click.echo(f"[INFO] Template written to {out}")
    click.echo(f"       Edit it and run: socc socdef validate {out}")
