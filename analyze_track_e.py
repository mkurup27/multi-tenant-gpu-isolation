#!/usr/bin/env python3
"""Reduce Track E to the self-inflicted-contention findings the article needs.

Three arms, three different questions:

  ramp_*           closed-loop concurrency sweep; where does the tail break?
  mixed_lengths_*  short and long prompts in one window, split by prompt class
                   so the question is what a long neighbour does to a short
                   request rather than what the mixture averages to
  kv_pressure_*    long generations at high concurrency; did KV pressure
                   actually materialise, per the gauge rather than per intent

    python3 analyze_track_e.py results/track-e --out-dir analysis
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

# vLLM exports these with label sets appended, so match on fragments.
WAITING = "num_requests_waiting"
RUNNING = "num_requests_running"
KV_USAGE = ("kv_cache_usage_perc", "gpu_cache_usage_perc")
PREEMPTIONS = "preemptions_total"


def percentile(values, pct):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    rank = max(0, min(len(values) - 1, int(round((pct / 100) * (len(values) - 1)))))
    return values[rank]


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def server_metric(summary, fragment, field):
    """Pull one field from the first server metric whose name matches."""
    metrics = summary.get("server_metrics", {}).get("metrics", {})
    fragments = fragment if isinstance(fragment, tuple) else (fragment,)
    for name, item in metrics.items():
        if any(f in name for f in fragments):
            value = item.get(field)
            if value is not None:
                return value
    return None


def load_requests(path):
    """Measured, completed requests only. Warm-up and drops are not results."""
    records = []
    if not path.is_file():
        return records
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            # The bench labels the measured window "measure"; warm-up is "warmup".
            if not str(record.get("phase", "")).startswith("measure"):
                continue
            if record.get("admission_dropped") or record.get("error"):
                continue
            if record.get("ttft_s") is None:
                continue
            records.append(record)
    return records


def load_case(directory):
    summary_path = directory / "victim.summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text())
    counts = summary.get("counts", {})
    ttft = summary.get("ttft_s", {})
    tpot = summary.get("tpot_s", {})
    e2e = summary.get("e2e_s", {})
    return {
        "tag": directory.name,
        "completed_ok": counts.get("completed_ok"),
        "request_errors": counts.get("request_errors"),
        "dropped_admissions": counts.get("dropped_admissions"),
        "ttft_p50": ttft.get("p50"),
        "ttft_p99": ttft.get("p99"),
        "ttft_cv": ttft.get("cv"),
        "tpot_p50": tpot.get("p50"),
        "e2e_p99": e2e.get("p99"),
        "tokens_per_s": summary.get("throughput", {}).get(
            "tokens_per_completion_span_s"),
        "waiting_max": server_metric(summary, WAITING, "p99"),
        "running_max": server_metric(summary, RUNNING, "p99"),
        "kv_usage_p50": server_metric(summary, KV_USAGE, "p50"),
        "kv_usage_max": server_metric(summary, KV_USAGE, "p99"),
        "preemptions": server_metric(summary, PREEMPTIONS, "delta"),
        "requests": load_requests(directory / "victim.jsonl"),
    }


def fmt(value, spec=".4g"):
    return format(value, spec) if isinstance(value, (int, float)) else "-"


def ramp_section(cases):
    ramp = sorted(
        (c for c in cases if c["tag"].startswith("ramp_c")),
        key=lambda c: int(c["tag"].split("_c")[1]),
    )
    if not ramp:
        return ["## Concurrency ramp", "", "No ramp cases found.", ""]

    baseline = ramp[0]
    lines = [
        "## Concurrency ramp, closed loop",
        "",
        f"Ratios are against concurrency {baseline['tag'].split('_c')[1]}, the "
        "lowest level run.",
        "",
        "| concurrency | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | tok/s | "
        "waiting | KV usage | preemptions |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for case in ramp:
        lines.append("| " + " | ".join([
            case["tag"].split("_c")[1],
            fmt(case["completed_ok"], "d") if case["completed_ok"] is not None else "-",
            fmt(case["request_errors"], "d") if case["request_errors"] is not None else "-",
            fmt(case["ttft_p50"]),
            fmt(case["ttft_p99"]),
            fmt(case["tpot_p50"]),
            fmt(case["tokens_per_s"], ".1f"),
            fmt(case["waiting_max"], ".1f"),
            fmt(case["kv_usage_max"], ".3f"),
            fmt(case["preemptions"], ".0f"),
        ]) + " |")

    lines += ["", "Relative to the lowest level:", ""]
    for metric, label in (("ttft_p50", "TTFT p50"), ("ttft_p99", "TTFT p99"),
                          ("tpot_p50", "TPOT p50"), ("tokens_per_s", "throughput")):
        reference = baseline.get(metric)
        if not reference:
            continue
        ratios = [
            f"c{c['tag'].split('_c')[1]}={c[metric] / reference:.2f}x"
            for c in ramp if c.get(metric)
        ]
        lines.append(f"- {label}: " + ", ".join(ratios))
    return lines + [""]


def mixed_section(cases):
    mixed = sorted((c for c in cases if c["tag"].startswith("mixed_lengths_")),
                   key=lambda c: c["tag"])
    if not mixed:
        return ["## Mixed prompt lengths", "", "No mixed-length cases found.", ""]

    lines = [
        "## Mixed prompt lengths, split by prompt class",
        "",
        "Every request below shared a window with the other class. Splitting by "
        "class asks what a long prompt does to a short one, which is the "
        "question an averaged window cannot answer.",
        "",
        "| case | prompt words | n | TTFT p50 | TTFT p99 | TPOT p50 |",
        "|---|---|---|---|---|---|",
    ]
    pooled = {}
    for case in mixed:
        by_class = {}
        for record in case["requests"]:
            by_class.setdefault(record.get("prompt_words_class"), []).append(record)
        for words in sorted(by_class, key=lambda w: (w is None, w)):
            group = by_class[words]
            ttfts = [r["ttft_s"] for r in group]
            tpots = [r.get("tpot_s") for r in group]
            pooled.setdefault(words, {"ttft": [], "tpot": []})
            pooled[words]["ttft"] += ttfts
            pooled[words]["tpot"] += [t for t in tpots if t is not None]
            lines.append("| " + " | ".join([
                case["tag"], str(words), str(len(group)),
                fmt(percentile(ttfts, 50)), fmt(percentile(ttfts, 99)),
                fmt(median(tpots)),
            ]) + " |")

    if len(pooled) >= 2:
        lines += ["", "Pooled across repeats:", ""]
        for words in sorted(pooled, key=lambda w: (w is None, w)):
            data = pooled[words]
            lines.append(
                f"- {words} words, n={len(data['ttft'])}: "
                f"TTFT p50 {fmt(percentile(data['ttft'], 50))}s, "
                f"p99 {fmt(percentile(data['ttft'], 99))}s, "
                f"TPOT p50 {fmt(median(data['tpot']))}s"
            )
        classes = sorted(w for w in pooled if w is not None)
        if len(classes) == 2:
            short, long_ = classes
            for label, key in (("TTFT p50", "ttft"), ("TPOT p50", "tpot")):
                pick = percentile if key == "ttft" else lambda v, _p: median(v)
                a = pick(pooled[short][key], 50)
                b = pick(pooled[long_][key], 50)
                if a and b:
                    lines.append(
                        f"- {label}, {long_}-word over {short}-word: {b / a:.2f}x"
                    )
    return lines + [""]


def kv_section(cases):
    kv = sorted((c for c in cases if c["tag"].startswith("kv_pressure_")),
                key=lambda c: c["tag"])
    if not kv:
        return ["## KV-cache pressure", "", "No KV-pressure cases found.", ""]

    lines = [
        "## KV-cache pressure",
        "",
        "Whether pressure materialised is decided by the KV gauge and the "
        "preemption delta, not by the intent of the configuration.",
        "",
        "| case | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | KV p50 | KV max "
        "| waiting | preemptions |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for case in kv:
        lines.append("| " + " | ".join([
            case["tag"],
            str(case["completed_ok"]), str(case["request_errors"]),
            fmt(case["ttft_p50"]), fmt(case["ttft_p99"]), fmt(case["tpot_p50"]),
            fmt(case["kv_usage_p50"], ".3f"), fmt(case["kv_usage_max"], ".3f"),
            fmt(case["waiting_max"], ".1f"), fmt(case["preemptions"], ".0f"),
        ]) + " |")

    peak = [c["kv_usage_max"] for c in kv if c["kv_usage_max"] is not None]
    preempted = [c["preemptions"] for c in kv if c["preemptions"] is not None]
    lines += [""]
    if peak:
        top = max(peak)
        verdict = (
            f"Peak KV-cache usage reached {top:.1%} of capacity."
            if top <= 1.5 else
            f"Peak KV-cache gauge read {top:.3f}."
        )
        if top < 0.9:
            verdict += (" The cache never approached exhaustion, so this arm did"
                        " not produce the intended pressure and must be reported"
                        " as such.")
        lines.append(verdict)
    if preempted:
        total = sum(preempted)
        lines.append(
            f"Preemptions across the arm: {total:.0f}."
            + ("" if total else " No request was ever preempted.")
        )
    return lines + [""]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    if not args.results.is_dir():
        parser.error(f"{args.results} does not exist; transfer Track E first")

    directories = [d for d in sorted(args.results.iterdir()) if d.is_dir()]
    cases = [c for c in (load_case(d) for d in directories) if c]
    if not cases:
        parser.error(f"no readable cases under {args.results}")

    incomplete = [d.name for d in directories if not (d / "COMPLETE").is_file()]
    if incomplete:
        print(f"warning: cases without COMPLETE: {incomplete}", file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    per_case = args.out_dir / "track_e_cases.csv"
    columns = [k for k in cases[0] if k != "requests"]
    with per_case.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for case in cases:
            writer.writerow(case)

    errors = sum(c["request_errors"] or 0 for c in cases)
    dropped = sum(c["dropped_admissions"] or 0 for c in cases)
    lines = [
        "# Track E: self-inflicted contention", "",
        f"Cases analysed: {len(cases)} of {len(directories)} directories. "
        f"Request errors across all cases: {errors}. "
        f"Client admission drops: {dropped}.",
        "",
    ]
    if incomplete:
        lines += [f"Cases missing a COMPLETE marker: {', '.join(incomplete)}.", ""]

    lines += ramp_section(cases)
    lines += mixed_section(cases)
    lines += kv_section(cases)

    report = args.out_dir / "track_e_summary.md"
    report.write_text("\n".join(lines) + "\n")

    print(f"cases analyzed: {len(cases)}")
    print(f"wrote {per_case}")
    print(f"wrote {report}")


if __name__ == "__main__":
    main()
