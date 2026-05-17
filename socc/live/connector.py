"""Live target SSH connector — extract and check DTB from a running board.

Workflow
────────
1. SSH into *target* (``user@host[:port]``).
2. Read ``/sys/firmware/fdt`` from the running kernel (binary DTB).
3. Transfer the binary to a local temp file.
4. Decompile DTB → DTS using ``dtc`` (must be on the local host or the
   remote target, searched in this order).
5. Parse the DTS and return a ``SoC`` model (+ the temp DTS path).

Requirements
────────────
- OpenSSH client (``ssh``, ``scp`` or ``sftp``) on the local host.
- ``dtc`` (Device Tree Compiler) on the local host, OR on the remote target.
- The remote target must allow SSH and read access to ``/sys/firmware/fdt``.

If ``dtc`` is absent on both sides, the function raises ``RuntimeError``
with a clear installation hint.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from socc.model import SoC


# ──────────────────────────────────────────────────────────────────────────────


def _ssh_args(target: str, port: Optional[int]) -> list:
    """Build base ssh argument list (no StrictHostKeyChecking for dev boards)."""
    args = ["ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10"]
    if port:
        args += ["-p", str(port)]
    return args


def _parse_target(target: str) -> Tuple[str, Optional[int]]:
    """Parse ``user@host`` or ``user@host:port`` → (host_str, port_or_None).

    Returns the full ``user@host`` string as *host_str* for use in ssh/scp.
    """
    # strip trailing slash just in case
    target = target.rstrip("/")
    if ":" in target.split("@")[-1]:
        at = target.rsplit(":", 1)
        host = at[0]
        try:
            port = int(at[1])
        except ValueError:
            host = target
            port = None
    else:
        host = target
        port = None
    return host, port


def _find_dtc_local() -> Optional[str]:
    """Return path to local ``dtc`` binary, or None."""
    return shutil.which("dtc")


def _dtc_available_on_remote(host: str, port: Optional[int]) -> bool:
    """Check if ``dtc`` exists on the remote target."""
    args = _ssh_args(host, port) + [host, "which dtc 2>/dev/null"]
    try:
        result = subprocess.run(args, capture_output=True, timeout=10)
        return result.returncode == 0
    except Exception:
        return False


def extract_live_dts(
    target: str,
    soc_name: str = "auto",
    timeout: int = 30,
) -> Tuple[SoC, str]:
    """Connect to *target* via SSH, extract the live FDT, and return a SoC model.

    Args:
        target:   ``user@host`` or ``user@host:port``
        soc_name: SoC identifier or ``"auto"`` for hostname-based detection.
        timeout:  SSH operation timeout in seconds.

    Returns:
        ``(SoC_model, local_dts_path)`` — the model and the temp DTS file path.
        The temp file is in ``/tmp/`` and persists until the next ``socc``
        invocation or manual deletion.

    Raises:
        RuntimeError: if SSH connection fails or neither local nor remote
                      ``dtc`` is found.
    """
    from socc.parser import parse_dts_file
    from socc.cli import _auto_detect_soc

    host, port = _parse_target(target)

    # ── Step 1: extract raw DTB from running kernel ────────────────────────
    dtb_tmp = tempfile.NamedTemporaryFile(
        suffix=".dtb", prefix="socc_live_", delete=False
    )
    dtb_path = dtb_tmp.name
    dtb_tmp.close()

    ssh_base = _ssh_args(host, port)

    # Use `dd` to read the FDT blob — more portable than cat for binary files
    extract_cmd = ssh_base + [host, "dd if=/sys/firmware/fdt bs=4096 2>/dev/null"]
    try:
        with open(dtb_path, "wb") as fout:
            result = subprocess.run(
                extract_cmd,
                stdout=fout,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"SSH command failed (exit {result.returncode}).  "
                f"Stderr: {result.stderr.decode(errors='replace').strip()}"
            )
        dtb_size = Path(dtb_path).stat().st_size
        if dtb_size < 64:
            raise RuntimeError(
                f"Extracted FDT is too small ({dtb_size} bytes). "
                "Check that /sys/firmware/fdt is accessible on the target "
                "(try: ssh {host} ls -la /sys/firmware/fdt)."
            )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"SSH connection to '{host}' timed out after {timeout}s.")
    except FileNotFoundError:
        raise RuntimeError(
            "OpenSSH client not found on this host.  "
            "Install it with:  brew install openssh  (macOS) or "
            "apt-get install openssh-client  (Debian/Ubuntu)."
        )

    # ── Step 2: decompile DTB → DTS ───────────────────────────────────────
    dts_path = dtb_path.replace(".dtb", ".dts")

    dtc_local = _find_dtc_local()
    if dtc_local:
        dtc_cmd = [dtc_local, "-I", "dtb", "-O", "dts", "-o", dts_path, dtb_path]
        try:
            subprocess.run(dtc_cmd, check=True, capture_output=True, timeout=30)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"dtc decompilation failed: {e.stderr.decode(errors='replace').strip()}"
            )
    else:
        # Try on the remote target
        if _dtc_available_on_remote(host, port):
            remote_dtb = f"/tmp/socc_live_{id(target)}.dtb"
            remote_dts = remote_dtb.replace(".dtb", ".dts")
            # push the local dtb to the remote, decompile there, pull back
            scp_push = _build_scp(host, port, dtb_path, f"{host}:{remote_dtb}")
            scp_pull = _build_scp(host, port, f"{host}:{remote_dts}", dts_path)
            dtc_remote = ssh_base + [host, f"dtc -I dtb -O dts -o {remote_dts} {remote_dtb}"]
            try:
                subprocess.run(scp_push, check=True, capture_output=True, timeout=30)
                subprocess.run(dtc_remote, check=True, capture_output=True, timeout=30)
                subprocess.run(scp_pull, check=True, capture_output=True, timeout=30)
                # cleanup remote
                subprocess.run(
                    ssh_base + [host, f"rm -f {remote_dtb} {remote_dts}"],
                    capture_output=True, timeout=10,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"Remote dtc decompilation failed: "
                    f"{e.stderr.decode(errors='replace').strip()}"
                )
        else:
            raise RuntimeError(
                "Device Tree Compiler (dtc) not found on local host or target.\n"
                "Install locally:  brew install dtc  (macOS) "
                "or  apt-get install device-tree-compiler  (Linux).\n"
                "Or on the target: apt-get install device-tree-compiler."
            )

    # ── Step 3: auto-detect SoC if needed ─────────────────────────────────
    if soc_name == "auto":
        # Use remote hostname as a hint
        try:
            hostname_cmd = ssh_base + [host, "cat /proc/device-tree/model 2>/dev/null || hostname"]
            res = subprocess.run(hostname_cmd, capture_output=True, timeout=10)
            hint = res.stdout.decode(errors="replace").strip().lower()
            soc_name = _auto_detect_soc(hint) if hint else "unknown"
        except Exception:
            soc_name = "unknown"

    # ── Step 4: parse DTS and build SoC model ─────────────────────────────
    model = parse_dts_file(dts_path, soc_name)
    return model, dts_path


def _build_scp(host: str, port: Optional[int], src: str, dst: str) -> list:
    """Return an scp command list."""
    cmd = ["scp", "-o", "StrictHostKeyChecking=no"]
    if port:
        cmd += ["-P", str(port)]
    cmd += [src, dst]
    return cmd


__all__ = ["extract_live_dts"]
