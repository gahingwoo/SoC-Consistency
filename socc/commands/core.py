"""Top-level socc commands: check, fix, autofix, diff, smart-diff,
explain, version, rules, init, self-update."""

from __future__ import annotations

import json as _json
import sys

import click

from socc import __version__
from socc.commands._shared import (
    ALL_SOC_CHOICES, FuzzySoCType, build_registry, auto_detect_soc,
    severity_tag, echo, load_config, filter_by_severity, SAMPLE_CONFIG,
    build_sample_model, parse_dts_file, parse_dts_cached, Checker, Path, Optional,
    suggest_rule_codes,
)

# ── Granular exit-code mapping ────────────────────────────────────────────────
# 0 = clean   1 = info only   2 = warnings present   3 = errors present
_EXIT_CODES = {"error": 3, "warning": 2, "info": 1}


def _compute_exit_code(violations: list, strict: bool = False) -> int:
    """Return the exit code for *violations* (0 = all clear).

    Default (non-strict): only errors produce a non-zero exit (exit 3).
    Warnings and info messages are printed but do not block CI.

    With strict=True: warnings also produce a non-zero exit (exit 2),
    info produces exit 1 — matching the full granular behaviour.
    """
    if not violations:
        return 0
    if strict:
        worst = max(_EXIT_CODES.get(v.severity, 0) for v in violations)
        return worst
    # Default: only errors are fatal
    has_error = any(_EXIT_CODES.get(v.severity, 0) >= 3 for v in violations)
    return 3 if has_error else 0


# ── check ──────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), default=None,
              metavar="SOC",
              help="Target SoC name (use 'auto' for filename-based auto-detection).")
@click.option("--format", "output_format",
              type=click.Choice(["text", "json", "sarif", "annotations"]),
              default=None,
              help="Output format (text, json, sarif, or annotations for GitHub Actions).")
@click.option("--min-severity", type=click.Choice(["error", "warning", "info"]),
              default=None, help="Minimum severity level to report.")
@click.option("--ignore-rule", multiple=True, metavar="CODE",
              help="Suppress a rule code (can be repeated).")
@click.option("--skip-rules", multiple=True, hidden=True,
              help="[Deprecated] Use --ignore-rule instead.")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off (default: auto-detect TTY).")
@click.option("--demo", is_flag=True,
              help="Run in demo mode using built-in sample data.")
@click.option("--netlist", "netlist_csv", type=click.Path(exists=True), default=None,
              metavar="CSV",
              help="Path to an EDA pin-assignment CSV for netlist cross-checking.")
@click.option("--watch", is_flag=True,
              help="Re-run whenever the DTS file changes (Ctrl-C to quit).")
@click.option("--no-cache", is_flag=True, default=False,
              help="Skip the parse cache and always re-parse from disk.")
@click.option("--rules-dir", "rules_dirs", multiple=True,
              type=click.Path(exists=True, file_okay=False),
              metavar="DIR",
              help="Load additional rule plugins from DIR (can be repeated).")
@click.option("--since", "git_since", default=None, metavar="REF",
              help="Only check DTS files changed since this git ref (e.g. HEAD~1, main).")
@click.option("--strict", is_flag=True, default=False,
              help="Exit non-zero for warnings (default: only errors produce a non-zero exit).")
def check(dts_file, soc, output_format, min_severity, ignore_rule,
          skip_rules, color, demo, netlist_csv, watch, no_cache,
          rules_dirs, git_since, strict):
    """Check a device tree for SoC consistency violations."""
    import os, time, hashlib

    cfg = load_config()
    resolved_soc           = soc or cfg.get("default_soc", "auto")
    resolved_format        = output_format or cfg.get("format", "text")
    resolved_min_severity  = min_severity or cfg.get("min_severity", "info")
    ignored                = set(cfg.get("ignore_rules", [])) | set(ignore_rule) | set(skip_rules)
    use_color              = color if color is not None else None

    # ── load project-level .socc_ignore ──────────────────────────────────────
    from socc.suppress import SuppressFilter
    suppress = SuppressFilter.load(Path(dts_file).parent if dts_file else Path("."))

    # ── git --since filter ────────────────────────────────────────────────────
    if git_since and dts_file:
        changed = _git_changed_files(git_since)
        abs_dts = str(Path(dts_file).resolve())
        if changed is not None and abs_dts not in changed:
            click.echo(
                click.style(f"[socc] No changes to {dts_file} since {git_since!r}. Skipped.",
                            fg="cyan"),
            )
            return

    def _run_once() -> int:
        """Execute one check pass.  Returns the exit code."""
        registry = build_registry(extra_rules_dirs=list(rules_dirs) if rules_dirs else None)

        # ── warn about unknown --ignore-rule codes ────────────────────────
        for bad in ignored:
            known_codes = {r.code for r in registry.list_all_rules()}
            if bad not in known_codes:
                suggestion = suggest_rule_codes(bad, registry)
                click.echo(
                    click.style(f"Warning: unknown rule code {bad!r}. ", fg="yellow")
                    + suggestion,
                    err=True,
                )

        if demo:
            echo("Running in demo mode...", color=use_color)
            model    = build_sample_model("rk3588")
            soc_name = "rk3588"
            sf       = None
            source_text = None
        else:
            if not dts_file:
                click.echo("Error: specify a device-tree file or use --demo", err=True)
                return 1
            try:
                echo(f"Loading device tree: {dts_file}", color=use_color)
                if resolved_soc == "auto":
                    soc_name = auto_detect_soc(Path(dts_file).name)
                else:
                    soc_name = resolved_soc
                sf    = str(Path(dts_file).resolve())
                source_text = Path(dts_file).read_text(errors="replace")
                model = (
                    parse_dts_file(dts_file, soc_name)
                    if no_cache
                    else parse_dts_cached(dts_file, soc_name)
                )
                echo(f"Loaded device tree (SoC: {soc_name})", color=use_color)
            except FileNotFoundError:
                click.echo(f"Error: file not found: {dts_file!r}", err=True)
                return 1
            except Exception as e:
                click.echo(f"Error: failed to parse device tree: {e}", err=True)
                return 1

        extra_metadata: dict = {}
        if netlist_csv:
            try:
                from socc.netlist.parser import parse_netlist_csv
                netlist_pins = parse_netlist_csv(netlist_csv)
                extra_metadata["netlist_pins"] = netlist_pins
                echo(f"Loaded netlist: {netlist_csv} ({len(netlist_pins)} pins)",
                     color=use_color)
            except Exception as e:
                click.echo(f"Warning: could not load netlist CSV: {e}", err=True)

        # ── try violation result cache (content-hash keyed) ───────────────
        rules_hash = hashlib.sha1(
            ",".join(sorted(r.code for r in registry.list_all_rules())).encode()
        ).hexdigest()[:12]  # noqa: S324

        violations = None
        if not no_cache and sf:
            from socc.cache import get_cached_violations, set_cached_violations
            violations = get_cached_violations(sf, soc_name, rules_hash)
            if violations is not None:
                echo(
                    click.style(
                        f"[cache] {len(violations)} violation(s) (content unchanged, "
                        f"skipped rule engine)",
                        fg="cyan"),
                    color=use_color,
                )

        if violations is None:
            checker    = Checker(registry)
            violations = checker.check(model, soc_name, extra_metadata=extra_metadata,
                                       source_file=sf)
            if not no_cache and sf:
                from socc.cache import set_cached_violations
                set_cached_violations(sf, soc_name, rules_hash, violations)
        else:
            checker = Checker(registry)

        if ignored:
            violations = [v for v in violations if v.code not in ignored]
        violations = filter_by_severity(violations, resolved_min_severity)

        # ── apply .socc_ignore + inline socc-ignore comments ─────────────
        violations = suppress.apply(violations, source_text)

        report = checker.generate_report(violations, resolved_format, color=use_color)
        click.echo(report)

        return _compute_exit_code(violations, strict=strict)

    if watch:
        if not dts_file:
            click.echo("Error: --watch requires a DTS file argument.", err=True)
            raise SystemExit(1)
        last_mtime = None
        click.echo(click.style(f"Watching {dts_file} — press Ctrl-C to quit.",
                               fg="cyan", bold=True))
        try:
            while True:
                try:
                    mtime = os.stat(dts_file).st_mtime
                except OSError:
                    time.sleep(0.5)
                    continue
                if mtime != last_mtime:
                    last_mtime = mtime
                    if last_mtime is not None:  # not the very first run
                        click.echo("\n" + click.style(
                            f"[socc watch] {dts_file} changed — re-checking...",
                            fg="cyan", bold=True))
                    _run_once()
                time.sleep(0.5)
        except KeyboardInterrupt:
            click.echo(click.style("\n[socc watch] stopped.", fg="cyan"))
        return

    exit_code = _run_once()
    if exit_code:
        raise SystemExit(exit_code)


# ── rules ──────────────────────────────────────────────────────────────────

@click.command()
def rules():
    """List all available rules (all vendors)."""
    registry = build_registry()
    all_rules = sorted(registry.list_all_rules(), key=lambda r: r.code)
    use_color = None
    click.echo("=" * 100)
    click.echo("SoC-Consistency Available Rules")
    click.echo("=" * 100)
    click.echo()
    for rule in all_rules:
        sym = severity_tag(rule.severity, use_color)
        click.echo(f"{sym} [{rule.code}] {rule.name}")
        click.echo(f"  Description: {rule.description}")
        click.echo()


# ── version ─────────────────────────────────────────────────────────────────

@click.command()
def version():
    """Show version information."""
    click.echo(f"socc version {__version__}")
    click.echo("SoC-Consistency Device Tree Checker")
    click.echo("Supported vendors: Rockchip, Allwinner, Amlogic, Qualcomm, NXP")


# ── fix ─────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None,
              help="Target SoC (default: auto-detect from filename).")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off.")
def fix(dts_file: str, soc: Optional[str], color: Optional[bool]):
    """Suggest automated fixes for common DTS violations."""
    use_color = color
    registry = build_registry()
    soc_name = soc if (soc and soc != "auto") else auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    checker = Checker(registry)
    violations = checker.check(model, soc_name)
    fixable = [v for v in violations if getattr(v, "suggestion", None)]
    if not fixable:
        click.echo("No auto-fixable violations found.")
        return

    if use_color is False:
        head = lambda s: s
        add = lambda s: s
    else:
        head = lambda s: click.style(s, fg="cyan", bold=True)
        add = lambda s: click.style(s, fg="green")

    click.echo(f"Found {len(fixable)} fixable violation(s) in {dts_file}:\n")
    for v in fixable:
        tag = severity_tag(v.severity, use_color)
        click.echo(f"{tag} [{v.code}] {v.message}")
        click.echo(head("  --- Suggested fix ---"))
        for line in v.suggestion.splitlines():
            click.echo(add(f"  + {line}"))
        click.echo()


# ── autofix ─────────────────────────────────────────────────────────────────

@click.command()
@click.argument("dts_file")
@click.option("--apply/--no-apply", default=False,
              help="Apply fixes to the file in-place (default: preview only)")
@click.option("--generate-patch", is_flag=True,
              help="Write a .patch file (does NOT modify the DTS)")
@click.option("-o", "--output", default=None, help="Patch output path (default: auto-named)")
@click.option("--color/--no-color", default=True)
def autofix(dts_file: str, apply: bool, generate_patch: bool,
            output: Optional[str], color: bool):
    """Auto-fix common DTS hardware constraint violations.

    \b
    Preview only (default):
      socc autofix board.dts

    Apply in-place:
      socc autofix board.dts --apply

    Generate a git-apply-ready patch:
      socc autofix board.dts --generate-patch -o 0001-socc-fix.patch
    """
    from socc.autofix import generate_fixes_from_dts, apply_fixes_to_file, render_patch_summary
    patch_set = generate_fixes_from_dts(dts_file)
    click.echo(render_patch_summary(patch_set, use_color=color))
    if not patch_set.fixes:
        return
    if generate_patch:
        patch_text = patch_set.as_unified_patch()
        out = output or f"0001-socc-autofix-{Path(dts_file).stem}.patch"
        Path(out).write_text(patch_text)
        click.echo(f"\n[SUCCESS] Patch written to {out}")
        click.echo(f"          Apply with: git apply {out}")
    elif apply:
        n = apply_fixes_to_file(dts_file, patch_set.fixes)
        click.echo(f"\n[SUCCESS] Applied {n} fix(es) to {dts_file}")
    else:
        click.echo("\n[INFO] Preview only. Use --apply or --generate-patch to modify the file.")


# ── diff ─────────────────────────────────────────────────────────────────────

@click.command()
@click.argument("before_dts", type=click.Path(exists=True))
@click.argument("after_dts", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), default=None,
              metavar="SOC",
              help="Target SoC (default: auto-detect from filename).")
@click.option("--color/--no-color", default=None,
              help="Force colored output on or off.")
@click.option("--format", "output_format",
              type=click.Choice(["text", "json", "annotations"]),
              default="text", help="Output format.")
@click.option("--ci", is_flag=True,
              help="CI mode: exit non-zero on ANY regression, not just errors.")
def diff(before_dts: str, after_dts: str, soc: Optional[str],
         color: Optional[bool], output_format: str, ci: bool):
    """Show violations introduced between two DTS versions (regressions only).

    \b
    Exit codes:
      0  No regressions.
      2  Regressions at warning level (--ci only).
      3  Regressions at error level.
    """
    use_color = color
    registry = build_registry()

    def _detect(path: str) -> str:
        if soc and soc != "auto":
            return soc
        return auto_detect_soc(Path(path).name)

    def _run(path: str, soc_name: str):
        try:
            model = parse_dts_cached(path, soc_name)
        except Exception as e:
            click.echo(f"Error parsing {path}: {e}", err=True)
            raise SystemExit(1)
        checker = Checker(registry)
        return checker.check(model, soc_name, source_file=str(Path(path).resolve()))

    violations_before = _run(before_dts, _detect(before_dts))
    violations_after  = _run(after_dts,  _detect(after_dts))

    before_keys = {(v.code, v.message) for v in violations_before}
    regressions = [v for v in violations_after if (v.code, v.message) not in before_keys]

    if output_format == "json":
        data = [{"code": v.code, "severity": v.severity, "message": v.message,
                 "location": v.location, "suggestion": v.suggestion} for v in regressions]
        click.echo(_json.dumps(data, indent=2))
    elif output_format == "annotations":
        checker_obj = Checker(registry)
        click.echo(checker_obj._generate_annotations_report(regressions))
    else:
        if not regressions:
            if use_color is not False:
                click.echo(click.style("No regressions detected.", fg="green", bold=True))
            else:
                click.echo("No regressions detected.")
        else:
            click.echo(f"Regressions introduced ({len(regressions)} new violation(s)):\n")
            for v in regressions:
                tag = severity_tag(v.severity, use_color)
                loc = f"  at {v.location}" if v.location else ""
                click.echo(f"  {tag} [{v.code}] {v.message}{loc}")
                if v.suggestion:
                    click.echo(f"       Fix: {v.suggestion.splitlines()[0]}")
                click.echo()

    # ── exit code ─────────────────────────────────────────────────────────
    if ci:
        exit_code = _compute_exit_code(regressions)
    else:
        exit_code = 3 if any(v.severity == "error" for v in regressions) else 0
    if exit_code:
        raise SystemExit(exit_code)


# ── semantic diff filter ─────────────────────────────────────────────────────
# Properties that actually affect hardware behaviour — used by --semantic.
_HW_CRITICAL_PROPS = frozenset({
    "compatible", "reg", "status", "clocks", "clock-frequency",
    "assigned-clocks", "assigned-clock-rates", "assigned-clock-parents",
    "interrupts", "interrupt-parent", "interrupts-extended",
    "resets", "reset-gpios",
    "power-domains",
    "bus-width", "max-link-speed", "num-lanes",
    "gpio",
    "pinctrl-0", "pinctrl-1",
    "dmas", "dma-channels",
    "iommus",
})
# Wildcard suffixes (matched with str.endswith)
_HW_SUFFIX = ("-supply", "-microvolt", "-microamp", "-ohms", "-gpio", "-gpios")
# Wildcard prefixes (matched with str.startswith)
_HW_PREFIX = ("pinctrl-",)


def _is_hw_critical(prop: Optional[str]) -> bool:
    """Return True if *prop* is hardware-relevant."""
    if prop is None:
        return False
    if prop in _HW_CRITICAL_PROPS:
        return True
    for s in _HW_SUFFIX:
        if prop.endswith(s):
            return True
    for p in _HW_PREFIX:
        if prop.startswith(p):
            return True
    return False


def _apply_semantic_filter(report: "SmartDiffReport") -> "SmartDiffReport":  # type: ignore[name-defined]
    """Return a copy of *report* with only hardware-relevant changes.

    Keeps:
    - CHANGED entries on hardware-critical properties
    - ADDED / REMOVED whole-node entries only when the node contains or
      changes a hardware-critical property (they are kept as-is because the
      content is meaningful — structural duplicates come from phandle nodes
      that the normaliser already strips)

    Discards:
    - ADDED / REMOVED entries for individual properties that are not
      hardware-relevant
    - CHANGED entries for non-hardware properties
    """
    from dataclasses import replace, fields
    from socc.smartdiff import SmartDiffReport

    kept = []
    for entry in report.entries:
        if entry.kind == "CHANGED" and _is_hw_critical(entry.prop):
            kept.append(entry)
        elif entry.kind in ("ADDED", "REMOVED") and entry.prop is None:
            # Whole-node add/remove — keep to show structural change
            kept.append(entry)
        # prop-level ADDED/REMOVED for non-hw props → dropped (label/phandle noise)
        elif entry.kind in ("ADDED", "REMOVED") and _is_hw_critical(entry.prop):
            kept.append(entry)

    filtered = SmartDiffReport(path_a=report.path_a, path_b=report.path_b, entries=kept)
    return filtered


# ── smart-diff ───────────────────────────────────────────────────────────────

@click.command("smart-diff")
@click.argument("file_a")
@click.argument("file_b")
@click.option("--format", "output_format", default="text",
              type=click.Choice(["text", "markdown", "json"]))
@click.option("--color/--no-color", default=True)
@click.option("-o", "--output", default=None)
@click.option("--semantic", is_flag=True, default=False,
              help="Only report changes to hardware-relevant properties "
                   "(ignores label renames, comment edits, phandle renumbering, "
                   "and purely structural node additions/removals).")
def smart_diff(file_a: str, file_b: str, output_format: str,
               color: bool, output: Optional[str], semantic: bool):
    """Semantic diff of two device tree files (.dts or .dtb).

    Strips labels, comments, phandle values, and node ordering — comparing
    only the hardware-relevant semantic content.

    Use --semantic to further filter out ADDED/REMOVED structural noise and
    focus exclusively on hardware-critical property value changes.
    """
    from socc.smartdiff import smart_diff as _smart_diff
    from socc.smartdiff import render_diff_text, render_diff_markdown, render_diff_json, DTBDecodeError
    try:
        report = _smart_diff(file_a, file_b)
    except DTBDecodeError as e:
        click.echo(f"[ERROR] {e}", err=True)
        raise SystemExit(1)

    if semantic:
        report = _apply_semantic_filter(report)

    if output_format == "markdown":
        text = render_diff_markdown(report)
    elif output_format == "json":
        text = render_diff_json(report)
    else:
        text = render_diff_text(report, use_color=color and output is None)

    if output:
        Path(output).write_text(text)
        click.echo(f"[INFO] Diff report written to {output}")
    else:
        click.echo(text)

    if report.total > 0:
        raise SystemExit(1)


# ── explain ──────────────────────────────────────────────────────────────────

@click.command()
@click.argument("node_or_name")
@click.argument("dts_file", type=click.Path(exists=True), required=False)
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), metavar="SOC", default=None,
              help="Target SoC (default: auto-detect from filename).")
@click.option("--list", "list_kb", is_flag=True, default=False,
              help="List all documented hardware blocks in the knowledge base.")
def explain(node_or_name: str, dts_file: Optional[str],
            soc: Optional[str], list_kb: bool):
    """Explain a DTS node or a rule code.

    \b
    Examples:
        socc explain BW-101                         # explain a rule
        socc explain uart0 board.dts --soc rk3588   # explain a DTS node
        socc explain --list                         # list all known blocks
    """
    import re as _re
    from socc.explain import explain_node, render_explain, list_knowledge_base

    if list_kb:
        click.echo(list_knowledge_base())
        return

    # ── Rule-code mode: argument looks like "BW-101" or "CK-106" ────────────
    if _re.fullmatch(r'[A-Z][A-Z0-9]+-\d+', node_or_name):
        registry = build_registry()
        matched = [r for r in registry.list_all_rules() if r.code == node_or_name]
        if not matched:
            # fuzzy fallback: prefix match
            matched = [r for r in registry.list_all_rules()
                       if r.code.startswith(node_or_name.split("-")[0])]
        if not matched:
            click.echo(
                click.style(f"No rule found for code {node_or_name!r}.", fg="red"),
                err=True,
            )
            click.echo("Try 'socc rules' to list all available codes.", err=True)
            raise SystemExit(1)

        rule = matched[0]
        _sev_color = {"error": "red", "warning": "yellow", "info": "cyan"}.get(
            rule.severity, "white"
        )

        # Try to use rich; fall back to plain text
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.markdown import Markdown

            console = Console()
            sev_label = click.style(rule.severity.upper(), fg=_sev_color, bold=True)
            body = (
                f"**Code:** `{rule.code}`  \n"
                f"**Severity:** {rule.severity.upper()}  \n"
                f"**Rule name:** {rule.name}\n\n"
                f"---\n\n"
                f"### What this rule checks\n{rule.description}\n\n"
            )
            if hasattr(rule, "impact") and rule.impact:
                body += f"### Impact if violated\n{rule.impact}\n\n"
            if hasattr(rule, "suggestion") and rule.suggestion:
                body += f"### How to fix\n{rule.suggestion}\n\n"
            console.print(Panel(Markdown(body), title=f"[bold]{rule.code}[/bold]",
                                border_style=_sev_color))
        except ImportError:
            # rich not installed — plain text
            sep = "=" * 72
            click.echo(sep)
            click.echo(f"  {rule.code}  [{rule.severity.upper()}]  {rule.name}")
            click.echo(sep)
            click.echo(f"\nDescription:\n  {rule.description}\n")
            if hasattr(rule, "impact") and rule.impact:
                click.echo(f"Impact:\n  {rule.impact}\n")
            if hasattr(rule, "suggestion") and rule.suggestion:
                click.echo(f"Fix:\n  {rule.suggestion}\n")
            click.echo(sep)
        return

    # ── DTS-node mode (original behaviour) ───────────────────────────────────
    if dts_file is None:
        click.echo("Error: DTS_FILE is required unless explaining a rule code or using --list.",
                   err=True)
        raise SystemExit(1)
    soc_name = soc or auto_detect_soc(Path(dts_file).name)
    try:
        model = parse_dts_file(dts_file, soc_name)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        raise SystemExit(1)
    result = explain_node(node_or_name, model)
    if result is None:
        click.echo(
            f"No node matching {node_or_name!r} found in {dts_file}.\n"
            "Try 'socc explain --list' to see documented blocks.",
            err=True)
        raise SystemExit(1)
    click.echo(render_explain(result))


# ── init ─────────────────────────────────────────────────────────────────────

@click.command("init")
@click.option("--output", default=".socc.yaml", show_default=True,
              help="Destination path for the config file.")
@click.option("--force", is_flag=True, help="Overwrite an existing config file.")
def init_cmd(output: str, force: bool):
    """Create a .socc.yaml config file with commented defaults."""
    dest = Path(output)
    if dest.exists() and not force:
        click.echo(f"Error: {output} already exists. Use --force to overwrite.", err=True)
        raise SystemExit(1)
    dest.write_text(SAMPLE_CONFIG)
    click.echo(f"Created {output}")


# ── self-update ──────────────────────────────────────────────────────────────

@click.command("self-update")
@click.option("--check-only", is_flag=True,
              help="Only check for a newer version; do not install.")
def self_update(check_only: bool):
    """Upgrade socc to the latest version from PyPI."""
    import json, urllib.request, subprocess
    pypi_url = "https://pypi.org/pypi/soc-consistency/json"
    click.echo(f"Current version : {__version__}")
    click.echo(f"Checking PyPI   : {pypi_url}")
    try:
        req = urllib.request.Request(pypi_url, headers={"User-Agent": f"socc/{__version__}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        latest = data["info"]["version"]
    except Exception as e:
        click.echo(f"Could not reach PyPI: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"Latest version  : {latest}")

    def _ver(v):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)

    if _ver(latest) <= _ver(__version__):
        click.echo(click.style("Already up-to-date.", fg="green", bold=True))
        return
    click.echo(click.style(f"\nNew version available: {latest}", fg="yellow", bold=True))
    if check_only:
        click.echo("Run 'socc self-update' (without --check-only) to install.")
        return
    click.echo(f"Installing socc {latest} ...\n")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "soc-consistency"],
        check=False)
    if result.returncode == 0:
        click.echo(click.style(f"\nUpgraded to socc {latest} successfully.", fg="green", bold=True))
        click.echo("Restart the terminal for the new version to take effect.")
    else:
        click.echo("pip upgrade failed.  Try manually:", err=True)
        click.echo("  pip install --upgrade soc-consistency", err=True)
        raise SystemExit(1)


# ── bootstrap ────────────────────────────────────────────────────────────────

@click.command("bootstrap")
@click.option("--from-mainline", "src_dir",
              type=click.Path(exists=True, file_okay=False),
              required=True,
              metavar="DIR",
              help="Linux mainline DTS directory to scan (e.g. arch/arm64/boot/dts/rockchip/).")
@click.option("--soc", default=None, metavar="NAME",
              help="Target SoC name (default: auto-detect from directory name).")
@click.option("-o", "--output-dir", default=None, metavar="DIR",
              help="Output directory for the generated YAML (default: data/soc/<vendor>/).")
@click.option("--verbose", is_flag=True, help="Print extraction details.")
def bootstrap(src_dir: str, soc: Optional[str], output_dir: Optional[str], verbose: bool):
    """Generate a SoC YAML constraint stub from Linux mainline .dtsi files.

    \b
    Example:
        socc bootstrap --from-mainline ./linux/arch/arm64/boot/dts/rockchip/ --soc rk3588
        socc bootstrap --from-mainline ./linux/arch/arm64/boot/dts/marvell/
    """
    from socc.bootstrap import bootstrap_from_directory
    try:
        out = bootstrap_from_directory(
            src_dir, soc, output_dir=output_dir, verbose=verbose
        )
        click.echo(click.style(f"[bootstrap] YAML stub written to: {out}", fg="green", bold=True))
        click.echo("Edit the file to add datasheet-level signal constraints, then run:")
        click.echo(f"  socc check board.dts --soc {out.stem}")
    except ValueError as e:
        click.echo(click.style(f"Error: {e}", fg="red"), err=True)
        raise SystemExit(1)


# ── install-hook ──────────────────────────────────────────────────────────────

_HOOK_SCRIPT = """\
#!/bin/sh
# socc pre-commit hook — installed by 'socc install-hook'
# Checks only DTS files that are staged for commit.
set -e

DTS_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\\.(dts|dtsi)$' || true)
if [ -z "$DTS_FILES" ]; then
    exit 0
fi

echo "[socc] Checking $(echo "$DTS_FILES" | wc -l | tr -d ' ') staged DTS file(s)…"
FAILED=0
for f in $DTS_FILES; do
    socc check "$f" --no-color --min-severity warning || FAILED=1
done

if [ "$FAILED" -ne 0 ]; then
    echo ""
    echo "[socc] Commit blocked: fix the violations above, or use --no-verify to skip."
    exit 1
fi
"""

@click.command("install-hook")
@click.option("--hook", "hook_name",
              type=click.Choice(["pre-commit", "pre-push"]),
              default="pre-commit", show_default=True,
              help="Which git hook to install.")
@click.option("--force", is_flag=True,
              help="Overwrite an existing hook file.")
@click.option("--uninstall", is_flag=True,
              help="Remove the socc hook.")
def install_hook(hook_name: str, force: bool, uninstall: bool):
    """Install (or remove) a git hook that runs socc on staged DTS files.

    \b
    Examples:
        socc install-hook               # installs .git/hooks/pre-commit
        socc install-hook --pre-push    # installs .git/hooks/pre-push
        socc install-hook --uninstall   # removes the hook
    """
    import stat as _stat

    # Find .git directory (walk up from cwd)
    cwd = Path.cwd()
    git_dir: Optional[Path] = None
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".git"
        if candidate.is_dir():
            git_dir = candidate
            break
        if (candidate.parent / ".git").is_file():
            # Submodule: .git is a file pointing to the real dir
            git_dir = candidate.parent / ".git"
            break

    if git_dir is None:
        click.echo("Error: not inside a git repository.", err=True)
        raise SystemExit(1)

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_path = hooks_dir / hook_name

    if uninstall:
        if hook_path.exists():
            hook_path.unlink()
            click.echo(click.style(f"Removed {hook_path}", fg="yellow"))
        else:
            click.echo(f"No hook found at {hook_path}.")
        return

    if hook_path.exists() and not force:
        click.echo(
            f"Error: {hook_path} already exists. Use --force to overwrite.",
            err=True,
        )
        raise SystemExit(1)

    hook_path.write_text(_HOOK_SCRIPT)
    # Make executable
    current = hook_path.stat().st_mode
    hook_path.chmod(current | _stat.S_IXUSR | _stat.S_IXGRP | _stat.S_IXOTH)

    click.echo(click.style(f"[socc] Installed {hook_name} hook → {hook_path}", fg="green", bold=True))
    click.echo("Staged DTS files will now be checked on every commit.")
    click.echo("Use 'git commit --no-verify' to bypass.")


# ── _git_changed_files (internal helper) ─────────────────────────────────────

def _git_changed_files(since_ref: str) -> Optional[set]:
    """Return a set of absolute paths changed since *since_ref*.

    Returns *None* if git is not available or the ref is invalid (so callers
    can fall back to running the full check).
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since_ref, "HEAD", "--"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return None
        repo_root_result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if repo_root_result.returncode != 0:
            return None
        repo_root = Path(repo_root_result.stdout.strip())
        changed = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                changed.add(str((repo_root / line).resolve()))
        return changed
    except Exception:
        return None


# ── decompile ────────────────────────────────────────────────────────────────

@click.command("decompile")
@click.argument("dtb_file", type=click.Path(exists=True))
@click.option("--soc", type=FuzzySoCType(ALL_SOC_CHOICES), default=None, metavar="SOC",
              help="Target SoC name — used to annotate known peripheral addresses.")
@click.option("-o", "--output", default=None, metavar="FILE",
              help="Write output to FILE (default: stdout).")
@click.option("--no-annotate", is_flag=True, default=False,
              help="Skip SoC-DB annotation and emit raw dtc output only.")
def decompile(dtb_file: str, soc: Optional[str], output: Optional[str],
              no_annotate: bool):
    """Decompile a binary .dtb to annotated .dts source.

    Uses dtc for the binary-to-text pass, then cross-references the SoC
    hardware database to add human-readable inline comments.

    \b
    Examples:
        socc decompile board.dtb --soc rk3588
        socc decompile board.dtb --soc rk3588 -o board_annotated.dts
        socc decompile board.dtb --no-annotate   # raw dtc output only
    """
    from socc.smartdiff import _decompile_dtb, DTBDecodeError

    p = Path(dtb_file)

    if p.suffix.lower() == ".dtb":
        try:
            raw_dts = _decompile_dtb(dtb_file)
        except DTBDecodeError as exc:
            click.echo(f"[ERROR] {exc}", err=True)
            raise SystemExit(1)
    else:
        raw_dts = p.read_text(errors="replace")

    if no_annotate:
        _decompile_emit(raw_dts, output)
        return

    soc_name = soc or auto_detect_soc(p.name)
    addr_db: dict = {}
    if soc_name:
        try:
            from socc.parser.dts_mapper import load_soc_constraints
            constraints = load_soc_constraints(soc_name)
            if constraints:
                for bank in constraints.get("gpio_banks", []):
                    base = bank.get("base")
                    if base:
                        b = int(base, 16) if isinstance(base, str) else int(base)
                        addr_db[b] = (
                            f"GPIO{bank.get('bank', '?')} "
                            f"({bank.get('pins', '?')}-pin, "
                            f"{bank.get('voltage', '?')}V)"
                        )
                for cp in constraints.get("clock_providers", []):
                    base = cp.get("base")
                    if base:
                        b = int(base, 16) if isinstance(base, str) else int(base)
                        addr_db[b] = (
                            f"{cp.get('name', 'CRU').upper()} - "
                            f"{cp.get('description', '')}"
                        )
        except Exception:
            pass

    annotated = _annotate_dts(raw_dts, addr_db, soc_name)
    _decompile_emit(annotated, output)
    if output:
        click.echo(
            click.style(f"[socc] Annotated DTS written to {output}", fg="green"),
            err=True,
        )


def _decompile_emit(text: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(text)
    else:
        click.echo(text, nl=False)


def _annotate_dts(raw: str, addr_db: dict, soc_name: Optional[str]) -> str:
    """Add inline comments to dtc-decompiled DTS text."""
    import re

    _NODE_ADDR  = re.compile(r'^(\s*)([A-Za-z0-9_,+/-]+)@([0-9a-fA-F]+)\s*\{')
    _STATUS_VAL = re.compile(r'status\s*=\s*"(okay|disabled|fail[^"]*)"')

    from socc import __version__
    soc_tag = f" for {soc_name}" if soc_name else ""
    banner = (
        "/*\n"
        f" * Decompiled by socc v{__version__}{soc_tag}\n"
        " * Peripheral addresses annotated from hardware constraint database.\n"
        " * This file is auto-generated - review before use.\n"
        " */\n\n"
    )

    lines = raw.splitlines(keepends=True)
    out   = [banner]

    for line in lines:
        nm = _NODE_ADDR.match(line)
        if nm:
            indent, node_name, addr_hex = nm.group(1), nm.group(2), nm.group(3)
            comment_parts = []
            try:
                addr_int = int(addr_hex, 16)
                if addr_int in addr_db:
                    comment_parts.append(addr_db[addr_int])
            except ValueError:
                pass

            if comment_parts:
                annotation = "  /* " + " | ".join(comment_parts) + " */"
                line = f"{indent}{node_name}@{addr_hex}{annotation} {{\n"

        out.append(line)

    return "".join(out)
