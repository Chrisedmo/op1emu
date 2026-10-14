"""Commands that record a perf capture or read what runs recorded."""
from __future__ import annotations

import json
import sys
from typing import List
from pathlib import Path

from .. import capture_set, census, compilation, diff, diverge, exits, perf, probes, service, trace
from .. import report as perf_report
from ..categories import Categories
from .. import marks as marks_file
from ..marks import parse_marks
from ..runner import snapshot_binary
from .common import REPORT_ERRORS, _check_args, _out, _preflight, _spec


def cmd_perf(args) -> int:
    _check_args(args.arg)  # perf's own options are appended last, so they win
    try:
        begin, end = marks_file.parse_phase(args.window)
        problem = perf.check_window(args.marks, (begin, end), args.until)
    except ValueError as error:
        problem = f"--window: {error}"
    problem = problem or perf.check_call_graph(args.event, args.call_graph)
    problem = problem or perf.preflight(args.event, ["-c", str(args.period)] if args.period else ["-F", str(args.freq)],
                                        args.call_graph)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    if not _preflight(args):
        return 2
    out = _out(args, "perf")
    spec = _spec(args, snapshot_binary(args.binary, out))
    # One capture keeps its old place; several are numbered, one run each.
    captures = [out / "capture"] if args.captures == 1 else [out / f"capture{i:02d}" for i in range(args.captures)]
    for capture in captures:
        result = perf.record(spec, capture, (begin, end), args.event, args.period, args.freq, args.call_graph)
        error = result.error if not result.reached else \
            perf.check_fired(args.marks, (begin, end), [m.name for m in result.marks])
        if error:
            print(f"capture failed: {error} (log: {result.directory / 'log.txt'})", file=sys.stderr)
            return 1
    try:
        if len(captures) == 1:
            print(perf_report.format_report(perf_report.analyze(captures[0], Categories.load(),
                                                                perf_report.Labels()), args.top))
        else:
            reports = capture_set.load(captures, Categories.load(), perf_report.Labels())
            print(capture_set.format_set(capture_set.aggregate(captures, reports), args.top))
    except REPORT_ERRORS as error:
        print(f"report: {error}", file=sys.stderr)
        return 1
    return 0


def cmd_annotate(args) -> int:
    try:
        print(perf.annotate(args.capture, args.pc, args.allow_imprecise, args.lines))
    except (OSError, ValueError, KeyError) as error:
        print(f"annotate: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_report(args) -> int:
    try:
        labels = perf_report.Labels(census.Symbols(args.symbols), [perf_report.PcRange.parse(r) for r in args.pc_range])
        captures = [c for path in args.capture for c in capture_set.captures_under(path)]
        if len(captures) > 1:
            if args.census or args.callers:
                raise ValueError("--census and --callers report one capture; give one")
            reports = capture_set.load(captures, Categories.load(args.categories), labels)
            data = capture_set.aggregate(captures, reports)
            text = capture_set.format_set(data, args.top)
        else:
            data = perf_report.analyze(captures[0], Categories.load(args.categories), labels, args.callers,
                                       args.census)
            text = perf_report.format_report(data, args.top, args.sort, args.min_runs)
    except REPORT_ERRORS as error:
        print(f"report: {error}", file=sys.stderr)
        return 2
    if args.json:
        args.json.write_text(json.dumps(data, indent=1) + "\n")
    print(text)
    return 0


def _trace_of(run: Path) -> dict:
    return trace.load(str(run / "trace.json" if run.is_dir() else run))


def cmd_probes(args) -> int:
    try:
        print(probes.summary(_trace_of(args.run), args.top))
    except (OSError, ValueError, KeyError) as error:
        print(f"probes: {error}", file=sys.stderr)
        return 2
    return 0


def _thread_names(pairs: List[str]) -> dict:
    names = {}
    for pair in pairs:
        value, _, name = pair.partition("=")
        if not name:
            raise SystemExit(f"--name must be VALUE=NAME, got {pair!r}")
        names[int(value, 16)] = name
    return names


def cmd_threads(args) -> int:
    names = _thread_names(args.name)
    try:
        data = _trace_of(args.run)
        print(probes.threads_report(data, args.probe, args.thread, names))
        if args.perfetto:
            probes.perfetto(data, args.probe, args.thread, names, args.perfetto)
            print(f"guest-time thread tracks: {args.perfetto}")
    except (OSError, ValueError, KeyError) as error:
        print(f"threads: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_diverge(args) -> int:
    try:
        print(diverge.report(args.a, args.b, census.Symbols(args.symbols)))
    except (OSError, ValueError) as error:
        print(f"diverge: {error}", file=sys.stderr)
        return 2
    return 0


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n")


def cmd_compile(args) -> int:
    path = args.run / "trace.json"
    try:
        trace_data = trace.load(str(path))
        window = marks_file.parse_phase(args.phase) if args.phase else None
        print(compilation.report(trace_data, window, args.top, census.Symbols(args.symbols)))
        if args.json:
            spans = compilation.translations(trace_data, window)
            _write_json(args.json, {
                "schema": "op1prof.compile.v1", "window": list(window) if window else None,
                "translations": [t.__dict__ for t in spans], "stages_wall_ms": compilation.stage_ms(trace_data, window),
                "codegen": compilation.codegen(spans) if compilation.has_shape(spans) else None})
    except (OSError, ValueError, KeyError) as error:
        print(f"compile: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_census(args) -> int:
    symbols = census.Symbols(args.symbols)
    try:
        begin, end = marks_file.parse_phase(args.phase)
        prefix = args.run / "census"
        data: dict = {"schema": "op1prof.census.v1", "phase": [begin, end]}
        if args.exits:
            found = exits.exits(prefix, trace.load(str(args.run / "trace.json")), begin, end)
            print(exits.exits_report(found, census.edge_phase(prefix, begin, end), args.top, symbols))
            data["exits"] = found["blocks"]
        elif args.memory:
            found = exits.exits(prefix, trace.load(str(args.run / "trace.json")), begin, end)
            memory = exits.memory_phase(prefix, begin, end)
            print(exits.memory_report(memory, found, args.top, symbols))
            data["memory"], data["exits"] = memory, found["blocks"]
        elif args.edges:
            print(census.edge_report(prefix, begin, end, args.top, symbols))
            data["edges"] = [{"from": a, "to": b, "count": n} for (a, b), n in census.edge_phase(prefix, begin, end).items()]
        elif args.mmio_first:
            fired = {m.name: m for m in parse_marks((args.run / "log.txt").read_text(errors="replace").splitlines())}
            missing = [name for name in (begin, end) if name not in fired]
            if missing:
                raise ValueError(f"marks did not fire in this run: {missing} (fired: {list(fired)})")
            window = (fired[begin].counters["cycles"], fired[end].counters["cycles"])
            rows = census.mmio_first(prefix, begin, end, window)
            print(census.mmio_first_report(rows, begin, end, args.top, symbols))
            data["mmio_first"] = rows
        else:
            print(census.report(prefix, begin, end, args.top, symbols))
            data["blocks"] = [{"pc": pc, "runs": r, "packets": n} for pc, (r, n) in census.phase(prefix, "pc", begin, end).items()]
            data["mmio"] = [{"addr": a, "reads": r, "writes": w} for a, (r, w) in census.phase(prefix, "mmio", begin, end).items()]
        if args.json:
            _write_json(args.json, data)
    except (OSError, ValueError, KeyError) as error:
        print(f"census: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_diff(args) -> int:
    symbols = census.Symbols(args.symbols)
    try:
        if args.what == "report":
            a, b = capture_set.captures_under(args.a), capture_set.captures_under(args.b)
            categories, labels = Categories.load(), perf_report.Labels(symbols)
            data = capture_set.diff_sets(a, capture_set.load(a, categories, labels),
                                         b, capture_set.load(b, categories, labels))
            print(capture_set.format_diff(data, args.top))
        elif args.what == "census":
            if not args.phase:
                raise ValueError("diff census needs --phase BEGIN:END")
            data = diff.census_diff(args.a, args.b, *marks_file.parse_phase(args.phase))
            print(diff.census_report(data, args.top, symbols))
        else:
            window = marks_file.parse_phase(args.phase) if args.phase else None
            data = diff.compile_diff(args.a, args.b, window, args.census)
            print(diff.compile_report(data, args.top, symbols))
    except REPORT_ERRORS as error:
        print(f"diff: {error}", file=sys.stderr)
        return 2
    if args.json:
        _write_json(args.json, data)
    return 0


def cmd_service(args) -> int:
    try:
        begin, end = marks_file.parse_phase(args.phase)
        data = service.phase(args.run / "census", begin, end)
        if args.capture:
            perf_report.check_same_run(args.capture, args.run)
            priced = perf_report.analyze(args.capture, Categories.load(), perf_report.Labels())
            # The capture's own binary snapshot: which classes define the method.
            binary = Path(json.loads((args.capture / "run" / "run.json").read_text())["binary"])
            data = service.join(data, priced, service.implementing_classes(binary))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"service: {error}", file=sys.stderr)
        return 2
    if args.json:
        _write_json(args.json, data)
    print(service.report(data, args.top))
    return 0
