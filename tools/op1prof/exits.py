"""Exit and memory censuses (--profile-census): after which blocks the host
had to act, what ends those blocks, and where the guest's loads and stores
go. op1emu returns to the host after every block; these say how often that
return was needed."""
from __future__ import annotations

import json
import struct
from collections import defaultdict
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import census, trace

EXIT = struct.Struct("<IIQQQQ")   # block, 0, runs needing the host for an MMR only, for host work only, both; accesses
WIDTHS = ("8", "16", "32")


def load_exits(path: Path) -> Dict[int, Tuple[int, int, int, int]]:
    data = path.read_bytes()
    if len(data) % EXIT.size:
        raise ValueError(f"{path}: truncated exit census ({len(data)} bytes)")
    return {pc: (a, b, c, d) for pc, _, a, b, c, d in EXIT.iter_unpack(data)}


def exit_phase(prefix: Path, begin: str, end: str) -> Dict[int, Tuple[int, int, int, int]]:
    first, last = (load_exits(Path(f"{prefix}.exit.{mark}")) for mark in (begin, end))
    out = {}
    for pc, values in last.items():
        before = first.get(pc, (0, 0, 0, 0))
        delta = tuple(v - b for v, b in zip(values, before))
        if any(d < 0 for d in delta):
            raise ValueError(f"exit counts went down for 0x{pc:08x}: {begin} is not before {end}")
        if any(delta):
            out[pc] = delta
    return out


def terminators(trace_data: dict, end: str) -> Dict[int, str]:
    """What ends each block, from its last translation before `end`."""
    stop = trace.marks(trace_data)[end]["ts"]
    out = {}
    for event in sorted(trace.jit_spans(trace_data), key=lambda e: e["ts"]):
        args = event["args"]
        if event["name"] == "translate" and event["ts"] < stop and args.get("ok", True) and "terminator" in args:
            out[args["pc"]] = args["terminator"]
    return out


def exits(prefix: Path, trace_data: dict, begin: str, end: str) -> dict:
    """Per block: runs, and how many of them needed the host. The host was
    needed after a run when the block accessed an MMR (it called into a
    device) or when the host then did something the guest can see (a
    productive device call, an event other than a raise, a new ILAT bit, a
    GP timer tick, the CEC entering or leaving an interrupt). The other runs
    could have continued in generated code."""
    runs = census.phase(prefix, "pc", begin, end)
    counts = exit_phase(prefix, begin, end)
    kinds = terminators(trace_data, end)
    blocks = []
    for pc, (block_runs, packets) in runs.items():
        mmio, host, both, accesses = counts.get(pc, (0, 0, 0, 0))
        if mmio + host + both > block_runs:
            raise ValueError(f"0x{pc:08x}: {mmio + host + both} runs needed the host, but it ran {block_runs} times")
        blocks.append({"pc": pc, "runs": block_runs, "packets": packets, "mmio": mmio, "host": host, "both": both,
                       "free": block_runs - mmio - host - both, "accesses": accesses,
                       "terminator": kinds.get(pc, "unknown")})
    extra = set(counts) - set(runs)
    if extra:
        raise ValueError(f"exit census has blocks the PC census never ran: {sorted(extra)[:5]}")
    return {"phase": [begin, end], "blocks": blocks}


def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:5.1f}%" if whole else "    -"


def exits_report(data: dict, edges: Dict[Tuple[int, int], int], limit: int, symbols: census.Symbols) -> str:
    begin, end = data["phase"]
    blocks = data["blocks"]
    total = {k: sum(b[k] for b in blocks) for k in ("runs", "mmio", "host", "both", "free")}
    runs = total["runs"]
    lines = [f"exits {begin} -> {end}: {runs:,} block runs, each followed by a return to the host",
             f"  needed for an MMR the block accessed:          {total['mmio']:>13,} {_pct(total['mmio'], runs)}",
             f"  needed for host work the guest could see:      {total['host']:>13,} {_pct(total['host'], runs)}",
             f"  needed for both:                               {total['both']:>13,} {_pct(total['both'], runs)}",
             f"  not needed (no MMR, nothing the guest saw):    {total['free']:>13,} {_pct(total['free'], runs)}",
             "", "by where decoding ended the block (a static label: a run can leave earlier, through a taken "
             "branch, a hardware loop or an exception)",
             f"{'block ends in':18s} {'blocks':>7} {'runs':>13} {'not needed':>13} {'share':>6}"]
    by_kind: Dict[str, list] = defaultdict(lambda: [0, 0, 0])
    for b in blocks:
        row = by_kind[b["terminator"]]
        row[0] += 1
        row[1] += b["runs"]
        row[2] += b["free"]
    for kind, (count, kind_runs, free) in sorted(by_kind.items(), key=lambda kv: -kv[1][2]):
        lines.append(f"{kind:18s} {count:>7,} {kind_runs:>13,} {free:>13,} {_pct(free, kind_runs)}")
    successors: Dict[int, Dict[int, int]] = defaultdict(dict)
    for (a, b), count in edges.items():
        successors[a][b] = count
    lines += ["", "blocks whose returns to the host were least needed (candidates to keep running in generated code):",
              f"{'block':>10} {'runs':>12} {'not needed':>12} {'share':>6} {'ends in':16s} {'top succ':>10} {'top %':>6}"
              f"  symbol"]
    for b in sorted(blocks, key=lambda b: -b["free"])[:limit]:
        succ = successors.get(b["pc"], {})
        top, top_count = max(succ.items(), key=lambda kv: kv[1]) if succ else (0, 0)
        lines.append(f"0x{b['pc']:08x} {b['runs']:>12,} {b['free']:>12,} {_pct(b['free'], b['runs'])} "
                     f"{b['terminator'][:16]:16s} 0x{top:08x} {_pct(top_count, b['runs'])}  {symbols.name(b['pc'])}")
    lines.append("Not needed means only that nothing the census sees required the host: the block still ends, "
                 "and its successor still has to be found.")
    return "\n".join(lines)


def load_memory(prefix: Path, mark: str) -> dict:
    path = Path(f"{prefix}.memory.{mark}")
    if not path.is_file():
        raise ValueError(f"{path}: no memory census (an emulator before it, or the mark did not fire)")
    data = json.loads(path.read_text())
    if data.get("schema") != "op1.memory.v1":
        raise ValueError(f"{path}: not a memory census (op1.memory.v1)")
    return data


def memory_phase(prefix: Path, begin: str, end: str) -> dict:
    a, b = load_memory(prefix, begin), load_memory(prefix, end)
    regions = []
    for x, y in zip(a["regions"], b["regions"]):
        reads = [q - p for p, q in zip(x["reads"], y["reads"])]
        writes = [q - p for p, q in zip(x["writes"], y["writes"])]
        if min(reads + writes) < 0:
            raise ValueError(f"memory counts went down for {y['name']}: {begin} is not before {end}")
        if any(reads) or any(writes):
            regions.append({"name": y["name"], "base": y["base"], "end": y["end"], "reads": reads, "writes": writes})
    return {"phase": [begin, end], "accesses": b["accesses"] - a["accesses"], "regions": regions}


def memory_report(memory: dict, exit_data: Optional[dict], limit: int, symbols: census.Symbols) -> str:
    begin, end = memory["phase"]
    total = memory["accesses"]
    lines = [f"memory {begin} -> {end}: {total:,} guest loads and stores, each a call from generated code into "
             "the host", "",
             f"{'device':18s} {'range':>21} " + " ".join(f"{'r' + w:>12}" for w in WIDTHS) + " "
             + " ".join(f"{'w' + w:>11}" for w in WIDTHS) + f" {'share':>6}"]
    for r in sorted(memory["regions"], key=lambda r: -(sum(r["reads"]) + sum(r["writes"]))):
        span = f"0x{r['base']:08x}-0x{r['end']:08x}" if r["name"] != "unmapped" else ""
        count = sum(r["reads"]) + sum(r["writes"])
        lines.append(f"{r['name'][:18]:18s} {span:>21} " + " ".join(f"{v:>12,}" for v in r["reads"]) + " "
                     + " ".join(f"{v:>11,}" for v in r["writes"]) + f" {_pct(count, total)}")
    if exit_data:
        blocks = sorted(exit_data["blocks"], key=lambda b: -b["accesses"])[:limit]
        lines += ["", f"{'block':>10} {'runs':>12} {'accesses':>13} {'acc/run':>8} {'share':>6}  symbol"]
        for b in blocks:
            lines.append(f"0x{b['pc']:08x} {b['runs']:>12,} {b['accesses']:>13,} "
                         f"{b['accesses'] / b['runs'] if b['runs'] else 0:8.1f} {_pct(b['accesses'], total)}  "
                         f"{symbols.name(b['pc'])}")
    return "\n".join(lines)
