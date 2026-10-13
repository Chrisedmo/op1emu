#!/usr/bin/env python3
"""Smoke test of a profiling build on a synthetic LDR (no firmware needed).

Runs op1emu with marks, trace and census on the one-RTS LDR of ldr_smoke.py
and a small erased NAND image, then checks what it recorded: the build line,
the start and range marks, a v4 trace with the JIT cache flush at the L1
entry, census dumps whose block-to-block transitions match the block runs,
and that wrong options fail before the CPU starts.

usage: profile_smoke.py OP1EMU
"""
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ldr_smoke import ROOT, TIMEOUT_S, make_ldr, tail  # noqa: E402


def run(op1emu, work, *extra):
    return subprocess.run([str(op1emu), "nand.bin", "tiny.ldr", "--nand-snapshot", "--headless", "--deterministic",
                           "--input-script", "script", *extra], cwd=work, capture_output=True, text=True,
                          timeout=TIMEOUT_S)


def records(path, fmt):
    data, size = path.read_bytes(), struct.calcsize(fmt)
    return [struct.unpack_from(fmt, data, at) for at in range(0, len(data), size)]


def check(op1emu, work):
    marks = {"nand_sha256": hashlib.sha256((work / "nand.bin").read_bytes()).hexdigest(),
             "marks": [{"name": "l1", "lo": "0xffa00000", "hi": "0xffa00001"}]}
    (work / "marks.json").write_text(json.dumps(marks))
    emu = run(op1emu, work, "--profile-marks", "marks.json", "--profile-trace", "trace.json",
              "--profile-census", "census")
    output = emu.stdout + emu.stderr
    if emu.returncode != 0:
        return f"op1emu exited {emu.returncode}\n{tail(output)}"
    lines = emu.stdout.splitlines()
    build = [json.loads(line.split(" ", 1)[1]) for line in lines if line.startswith("[build] ")]
    if len(build) != 1 or build[0].get("schema") != "op1.build.v1" or build[0].get("profiling") is not True:
        return f"no [build] line naming a profiling build\n{tail(output)}"
    fired = [line.split()[1] for line in lines if line.startswith("[mark] ")]
    if fired != ["start", "l1"]:
        return f"marks fired {fired}, expected start and l1\n{tail(output)}"
    trace = json.loads((work / "trace.json").read_text())
    if trace.get("schema") != "op1.trace.v4" or trace.get("build") != build[0] or trace.get("dropped"):
        return f"trace header: {({k: v for k, v in trace.items() if k != 'traceEvents'})}"
    flushes = [e for e in trace["traceEvents"] if e["name"] == "invalidate"]
    if not flushes or any(e.get("cat") != "jit" or "cpu_ns" not in e["args"] for e in flushes):
        return f"no invalidate span with cpu_ns at the L1 entry: {flushes}"
    for mark in fired:
        runs = {pc: count for pc, count, _ in records(work / f"census.pc.{mark}", "<IQQ")}
        out = {}
        for source, _, count in records(work / f"census.edge.{mark}", "<IIQ"):
            out[source] = out.get(source, 0) + count
        # A transition counts with the block it leaves, before a mark at the
        # next block dumps: at every mark, out of each block equals its runs.
        if out != runs:
            return f"census at {mark}: transitions out {out} do not match block runs {runs}"

    # Wrong settings fail before the CPU starts.
    (work / "other.json").write_text(json.dumps({**marks, "nand_sha256": "0" * 64}))
    for extra, expected in (
            (["--profile-marks", "other.json"], "is for NAND sha256"),
            (["--profile-marks", "marks.json", "--profile-until", "nowhere"], "unknown mark"),
            (["--profile-trace-events", "10"], "add it"),
            (["--profile-trace", "t.json", "--profile-trace-events", "0"], "Invalid --profile-trace-events")):
        emu = run(op1emu, work, *extra)
        output = emu.stdout + emu.stderr
        if emu.returncode == 0 or expected not in output or "Start executing DXE" in output:
            return f"{extra}: expected a refusal mentioning {expected!r}\n{tail(output)}"
    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    op1emu = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="op1-profile-smoke-") as tmp:
        work = Path(tmp)
        (work / "gui").mkdir()
        (work / "gui" / "ui.json").symlink_to(ROOT / "gui" / "ui.json")
        (work / "tiny.ldr").write_bytes(make_ldr())
        (work / "nand.bin").write_bytes(b"\xff" * 4096)
        (work / "script").write_text("wait 30000\nquit\n")
        problem = check(op1emu, work)
    if problem:
        print(f"profile-smoke: {problem}")
        return 1
    print("profile-smoke: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.TimeoutExpired as e:
        print(f"timed out after {TIMEOUT_S}s: {e.cmd}")
        sys.exit(1)
