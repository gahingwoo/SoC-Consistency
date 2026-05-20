"""socc generate <subcommand> — code and file generation commands."""

from __future__ import annotations

import click

from socc.commands._shared import (
    ALL_SOC_CHOICES, FuzzySoCType, auto_detect_soc,
    parse_dts_file, parse_dts_cached, Path, Optional,
)


@click.group("generate")
def generate_group():
    """Generate QEMU scripts, C headers, test scripts, diagrams, and reports."""


# ── generate qemu ─────────────────────────────────────────────────────────────

@generate_group.command("qemu")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--format", "fmt", type=click.Choice(["cmd", "machine-c"]),
              default="cmd", show_default=True,
              help="cmd: shell script (default).  machine-c: QEMU C machine skeleton.")
@click.option("--kernel", default="Image", show_default=True, metavar="FILE")
@click.option("--dtb", default="board.dtb", show_default=True, metavar="FILE")
@click.option("-o", "--output", default=None, metavar="FILE")
def generate_qemu(dts_file: str, soc: Optional[str], fmt: str,
                  kernel: str, dtb: str, output: Optional[str]):
    """Generate a QEMU launch script or C machine skeleton from a DTS file.

    \b
    Examples:
        socc generate qemu board.dts
        socc generate qemu board.dts --format machine-c -o hw/arm/myboard.c
    """
    from socc.qemu import build_qemu_spec, render_qemu_command, render_qemu_machine_c
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    spec = build_qemu_spec(model)
    if fmt == "machine-c":
        text = render_qemu_machine_c(spec)
        default_out = Path(dts_file).stem + "_machine.c"
    else:
        text = render_qemu_command(spec, kernel=kernel, dtb=dtb)
        default_out = "launch_qemu.sh"
    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"QEMU config written to: {output}")
    else:
        out_path = default_out
        Path(out_path).write_text(text, encoding="utf-8")
        click.echo(text)
        click.echo(f"\n# Written to: {out_path}", err=True)


# ── generate tests ────────────────────────────────────────────────────────────

@generate_group.command("tests")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("-o", "--output", default=None, metavar="FILE")
def generate_tests(dts_file: str, soc: Optional[str], output: Optional[str]):
    """Generate a bash bring-up test script from a DTS file.

    \b
    Examples:
        socc generate tests board.dts
        socc generate tests board.dts -o rock5b_bringup.sh
    """
    from socc.codegen.bringup import generate_tests as _gen_tests
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    script = _gen_tests(model, dts_name=Path(dts_file).name)
    out_path = output or "bring_up.sh"
    Path(out_path).write_text(script, encoding="utf-8")
    click.echo(f"Bring-up test script written to: {out_path}")
    click.echo(f"Run on target:  chmod +x {out_path} && sudo ./{out_path}")


# ── generate saleae ───────────────────────────────────────────────────────────

@generate_group.command("saleae")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("-o", "--output", default=None, metavar="FILE")
def generate_saleae(dts_file: str, soc: Optional[str], output: Optional[str]):
    """Generate a Saleae Logic 2 workspace JSON from a DTS file.

    \b
    Examples:
        socc generate saleae board.dts
        socc generate saleae board.dts -o debug_session.json
    """
    from socc.codegen.saleae import render_saleae_workspace
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    json_str = render_saleae_workspace(model, dts_name=Path(dts_file).name)
    out_path = output or (Path(dts_file).stem + "_saleae.json")
    Path(out_path).write_text(json_str, encoding="utf-8")
    click.echo(f"Saleae Logic 2 workspace written to: {out_path}")


# ── generate headers ──────────────────────────────────────────────────────────

@generate_group.command("headers")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("-o", "--output", default=None, metavar="FILE")
@click.option("--stdout", "to_stdout", is_flag=True, default=False,
              help="Print header to stdout instead of writing a file.")
@click.option("--no-regulators", "skip_regulators", is_flag=True, default=False)
def generate_headers(dts_file: str, soc: Optional[str], output: Optional[str],
                     to_stdout: bool, skip_regulators: bool):
    """Generate a bare-metal C header with peripheral base addresses and IRQs.

    \b
    Examples:
        socc generate headers board.dts > soc_hw.h
        socc generate headers board.dts -o include/soc_hw.h
    """
    from socc.codegen.headers import generate_headers as _gen_headers
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    header = _gen_headers(model, dts_name=Path(dts_file).name,
                          include_regulators=not skip_regulators)
    if to_stdout:
        click.echo(header, nl=False)
    else:
        out_path = output or (Path(dts_file).stem + "_hw.h")
        Path(out_path).write_text(header, encoding="utf-8")
        click.echo(f"C header written to: {out_path}")


# ── generate diagram ──────────────────────────────────────────────────────────

@generate_group.command("diagram")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--format", "fmt", type=click.Choice(["mermaid", "plantuml", "dot"]),
              default="mermaid", show_default=True)
@click.option("--type", "diagram_type",
              type=click.Choice(["power", "clock", "bus", "full"]),
              default="full", show_default=True)
@click.option("-o", "--output", default=None, metavar="FILE")
def generate_diagram(dts_file: str, soc: Optional[str], fmt: str,
                     diagram_type: str, output: Optional[str]):
    """Generate a hardware topology diagram (Mermaid, PlantUML, or DOT).

    \b
    Examples:
        socc generate diagram board.dts --format mermaid >> README.md
        socc generate diagram board.dts --format dot -o topology.dot
    """
    from socc.diagram import generate_diagram as _gen_diagram
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    text = _gen_diagram(model, fmt=fmt, diagram_type=diagram_type)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        click.echo(f"Diagram written to: {output}")
    else:
        click.echo(text)


# ── generate compliance ───────────────────────────────────────────────────────

@generate_group.command("compliance")
@click.argument("dts_file")
@click.option("--standard", default="iso26262-asil-b", show_default=True,
              type=click.Choice([
                  "iso26262-asil-a", "iso26262-asil-b",
                  "iso26262-asil-c", "iso26262-asil-d",
                  "iec61508-sil1", "iec61508-sil2",
                  "iec61508-sil3", "iec61508-sil4",
              ]))
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown"]))
@click.option("--soc", default="auto")
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
def generate_compliance(dts_file: str, standard: str, output_format: str,
                        soc: Optional[str], color: bool, output: Optional[str]):
    """Generate an ISO 26262 / IEC 61508 functional-safety isolation report."""
    from socc.compliance import (generate_compliance_report,
                                 render_compliance_markdown, render_compliance_text)
    soc_model = parse_dts_file(dts_file)
    report = generate_compliance_report(soc_model, dts_path=dts_file, standard_id=standard)
    if output_format == "markdown":
        text = render_compliance_markdown(report)
    else:
        text = render_compliance_text(report, use_color=color and output is None)
    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Compliance report written to {output}")
    else:
        click.echo(text)
    if not report.overall_pass and report.safety_nodes:
        raise SystemExit(1)


# ── generate report ───────────────────────────────────────────────────────────

@generate_group.command("report")
@click.argument("dts_file")
@click.option("--format", "output_format", default="html",
              type=click.Choice(["html"]))
@click.option("--soc", default="auto")
@click.option("-o", "--output", default=None)
def generate_report(dts_file: str, output_format: str,
                    soc: Optional[str], output: Optional[str]):
    """Generate a self-contained HTML architecture report.

    \b
    Example:
        socc generate report board.dts -o report.html
    """
    from socc.report import generate_html_report
    from socc.commands._shared import build_registry, Checker
    soc_model = parse_dts_file(dts_file)
    registry = build_registry()
    detected_soc = soc if soc != "auto" else auto_detect_soc(Path(dts_file).name)
    checker = Checker(registry, soc_name=detected_soc)
    raw_violations = checker.check(soc_model)
    violations = [{"severity": v.severity, "node_path": v.node_path,
                   "description": v.message, "fix": getattr(v, "suggestion", "")}
                  for v in raw_violations]
    html_text = generate_html_report(soc_model, dts_path=dts_file, violations=violations)
    out = output or f"{Path(dts_file).stem}-report.html"
    Path(out).write_text(html_text)
    click.echo(f"[INFO] HTML report written to {out}")
    click.echo(f"       Open in browser: file://{Path(out).resolve()}")


# ── generate ci ───────────────────────────────────────────────────────────────

@generate_group.command("ci")
@click.option("--platform", "platform",
              type=click.Choice(["github", "gitlab", "both"]),
              default="github", show_default=True,
              help="Target CI platform.")
@click.option("--soc", default="auto", show_default=True, metavar="SOC",
              help="Default SoC for the workflow (auto = filename-based detection).")
@click.option("--strict", "ci_strict", is_flag=True, default=False,
              help="Fail the CI job on warnings as well as errors (--strict mode).")
@click.option("--dts-glob", default="**/*.dts", show_default=True, metavar="GLOB",
              help="Glob pattern for DTS files to check in the repo.")
@click.option("-o", "--output-dir", "output_dir", default=".", show_default=True,
              metavar="DIR",
              help="Directory where CI config file(s) will be written.")
def generate_ci(platform: str, soc: str, ci_strict: bool,
                dts_glob: str, output_dir: str):
    """Generate a CI/CD workflow that runs socc on every pull request.

    \b
    Creates ready-to-use config files:
        GitHub Actions  →  .github/workflows/socc-check.yml
        GitLab CI       →  .gitlab-ci.yml  (socc-check job)

    \b
    Examples:
        socc generate ci
        socc generate ci --platform gitlab --strict
        socc generate ci --soc rk3588 --dts-glob "arch/arm64/boot/dts/**/*.dts"
    """
    from socc.codegen.ci import render_github_actions_workflow, render_gitlab_ci_job

    out_dir = Path(output_dir)
    strict_flag = "--strict" if ci_strict else ""
    soc_flag = f"--soc {soc}" if soc != "auto" else ""

    if platform in ("github", "both"):
        gh_dir = out_dir / ".github" / "workflows"
        gh_dir.mkdir(parents=True, exist_ok=True)
        gh_path = gh_dir / "socc-check.yml"
        content = render_github_actions_workflow(
            dts_glob=dts_glob, soc_flag=soc_flag, strict_flag=strict_flag,
        )
        gh_path.write_text(content, encoding="utf-8")
        click.echo(f"GitHub Actions workflow written to: {gh_path}")
        click.echo("  Add to your repo:  git add .github/workflows/socc-check.yml")

    if platform in ("gitlab", "both"):
        gl_path = out_dir / ".gitlab-ci.yml"
        content = render_gitlab_ci_job(
            dts_glob=dts_glob, soc_flag=soc_flag, strict_flag=strict_flag,
        )
        gl_path.write_text(content, encoding="utf-8")
        click.echo(f"GitLab CI config written to: {gl_path}")


# ── generate docs ─────────────────────────────────────────────────────────────

@generate_group.command("docs")
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None)
@click.option("--format", "fmt", type=click.Choice(["markdown", "html"]),
              default="markdown", show_default=True,
              help="Output format.")
@click.option("-o", "--output", default=None, metavar="FILE",
              help="Output file (default: <stem>-docs.md or <stem>-docs.html).")
def generate_docs(dts_file: str, soc: Optional[str], fmt: str, output: Optional[str]):
    """Generate human-readable hardware documentation from a DTS file.

    Produces a structured document listing all enabled peripherals, their
    base addresses, clock rates, voltage rails, GPIO assignments, and
    interrupt numbers — ready to share with hardware teams.

    \b
    Examples:
        socc generate docs board.dts
        socc generate docs board.dts --format html -o board-spec.html
    """
    from socc.codegen.docs import render_dts_docs

    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)

    text = render_dts_docs(model, dts_path=dts_file, soc_name=soc_name, fmt=fmt)
    stem = Path(dts_file).stem
    default_out = f"{stem}-docs.md" if fmt == "markdown" else f"{stem}-docs.html"
    out_path = output or default_out
    Path(out_path).write_text(text, encoding="utf-8")
    click.echo(f"Hardware documentation written to: {out_path}")
