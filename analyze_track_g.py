#!/usr/bin/env python3
"""Reduce Track G to the three questions partitioning actually raises.

  g1 vs g2   does a slice contain a hostile neighbour?
  g1 vs g4   what does a slice cost when nobody is hostile?
  g3 vs g4   at equal offered load, what does the pair deliver against the
             undivided card, in throughput and in KV capacity?

g3 is a pair, so its throughput is summed across both slices before any
comparison; its latency is reported per slice, because a request is served by
one slice and never by both.

    python3 analyze_track_g.py results/track-g --out-dir analysis
"""

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

KIND_ORDER = ["g1_mig_solo", "g2_mig_antagonist", "g3_mig_dual", "g4_undivided"]
KIND_LABEL = {
    "g1_mig_solo": "MIG slice, neighbour idle",
    "g2_mig_antagonist": "MIG slice, HBM antagonist next door",
    "g3_mig_dual": "two MIG slices, 1 rps each",
    "g4_undivided": "undivided GPU, 2 rps",
}
KV_TOKENS = re.compile(r"GPU KV cache size:\s*([\d,]+)\s*tokens")


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def spread(values):
    """Min-max across repeats. Three repeats do not support a bootstrap CI."""
    values = [v for v in values if v is not None]
    return (min(values), max(values)) if values else (None, None)


def load_arm(path):
    if not path.is_file():
        return None
    summary = json.loads(path.read_text())
    counts = summary.get("counts", {})
    return {
        "completed_ok": counts.get("completed_ok"),
        "request_errors": counts.get("request_errors"),
        "dropped_admissions": counts.get("dropped_admissions"),
        "ttft_p50": summary.get("ttft_s", {}).get("p50"),
        "ttft_p99": summary.get("ttft_s", {}).get("p99"),
        "ttft_cv": summary.get("ttft_s", {}).get("cv"),
        "tpot_p50": summary.get("tpot_s", {}).get("p50"),
        "e2e_p99": summary.get("e2e_s", {}).get("p99"),
        "tokens_per_s": summary.get("throughput", {}).get(
            "tokens_per_completion_span_s"),
    }


def load_case(directory):
    arms = [a for a in (load_arm(directory / name) for name in
                        ("victim.summary.json", "victim_b.summary.json")) if a]
    if not arms:
        return None
    meta = {}
    meta_path = directory / "case.meta"
    kv = []
    if meta_path.is_file():
        text = meta_path.read_text()
        kv = [int(m.replace(",", "")) for m in KV_TOKENS.findall(text)]
        for line in text.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                meta[key.strip()] = value.strip()
    return {
        "tag": directory.name,
        "kind": meta.get("case", directory.name.rsplit("_r", 1)[0]),
        "repeat": meta.get("repeat"),
        "arms": arms,
        # Latency belongs to a slice; throughput belongs to the configuration.
        "ttft_p50": median([a["ttft_p50"] for a in arms]),
        "ttft_p99": median([a["ttft_p99"] for a in arms]),
        "tpot_p50": median([a["tpot_p50"] for a in arms]),
        "tokens_per_s": sum(a["tokens_per_s"] for a in arms
                            if a["tokens_per_s"] is not None) or None,
        "completed_ok": sum(a["completed_ok"] or 0 for a in arms),
        "request_errors": sum(a["request_errors"] or 0 for a in arms),
        "dropped_admissions": sum(a["dropped_admissions"] or 0 for a in arms),
        "kv_tokens_per_engine": median(kv) if kv else None,
        "kv_tokens_total": sum(kv) if kv else None,
    }


def fmt(value, spec=".4g"):
    return format(value, spec) if isinstance(value, (int, float)) else "-"


def ratio_line(label, numerator, denominator, unit=""):
    if not numerator or not denominator:
        return f"- {label}: insufficient data"
    return (f"- {label}: {numerator:.4g}{unit} vs {denominator:.4g}{unit} = "
            f"{numerator / denominator:.2f}x")


def by_kind(cases):
    grouped = {}
    for case in cases:
        grouped.setdefault(case["kind"], []).append(case)
    return grouped


def summary_table(grouped):
    lines = [
        "| configuration | repeats | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 "
        "| TPOT range | tok/s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in KIND_ORDER:
        cases = grouped.get(kind)
        if not cases:
            continue
        tpots = [c["tpot_p50"] for c in cases]
        low, high = spread(tpots)
        lines.append("| " + " | ".join([
            f"{KIND_LABEL.get(kind, kind)} (`{kind}`)",
            str(len(cases)),
            str(sum(c["completed_ok"] for c in cases)),
            str(sum(c["request_errors"] for c in cases)),
            fmt(median([c["ttft_p50"] for c in cases])),
            fmt(median([c["ttft_p99"] for c in cases])),
            fmt(median(tpots)),
            f"{fmt(low)}-{fmt(high)}" if low is not None else "-",
            fmt(median([c["tokens_per_s"] for c in cases]), ".1f"),
        ]) + " |")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis"))
    # Track B's undivided control, so the partitioned numbers can be read on the
    # same scale as the shared-card contention results.
    parser.add_argument("--track-b-baseline-tpot", type=float, default=None,
                        help="TPOT p50 of the Track B no-antagonist control")
    parser.add_argument("--track-b-contended-tpot", type=float, default=None,
                        help="TPOT p50 of the Track B HBM antagonist arm")
    args = parser.parse_args()

    if not args.results.is_dir():
        parser.error(f"{args.results} does not exist; transfer Track G first")

    directories = [d for d in sorted(args.results.iterdir()) if d.is_dir()]
    cases = [c for c in (load_case(d) for d in directories) if c]
    if not cases:
        parser.error(f"no readable cases under {args.results}")

    incomplete = [d.name for d in directories if not (d / "COMPLETE").is_file()]
    if incomplete:
        print(f"warning: cases without COMPLETE: {incomplete}", file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    per_case = args.out_dir / "track_g_cases.csv"
    columns = [k for k in cases[0] if k != "arms"]
    with per_case.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for case in cases:
            writer.writerow(case)

    grouped = by_kind(cases)
    med = {k: {
        "ttft_p50": median([c["ttft_p50"] for c in v]),
        "tpot_p50": median([c["tpot_p50"] for c in v]),
        "tokens_per_s": median([c["tokens_per_s"] for c in v]),
        "kv_engine": median([c["kv_tokens_per_engine"] for c in v]),
        "kv_total": median([c["kv_tokens_total"] for c in v]),
    } for k, v in grouped.items()}

    lines = [
        "# Track G: MIG partitioning", "",
        f"Cases analysed: {len(cases)} of {len(directories)} directories. "
        f"Request errors: {sum(c['request_errors'] for c in cases)}. "
        f"Client admission drops: {sum(c['dropped_admissions'] for c in cases)}.",
        "",
    ]
    if incomplete:
        lines += [f"Cases missing a COMPLETE marker: {', '.join(incomplete)}.", ""]

    lines += ["## Per-configuration medians", ""] + summary_table(grouped) + [""]

    g1, g2 = med.get("g1_mig_solo"), med.get("g2_mig_antagonist")
    g3, g4 = med.get("g3_mig_dual"), med.get("g4_undivided")

    lines += ["## Does a slice contain a hostile neighbour?", ""]
    if g1 and g2:
        lines += [
            ratio_line("TPOT p50, antagonist over idle neighbour",
                       g2["tpot_p50"], g1["tpot_p50"], "s"),
            ratio_line("TTFT p50, antagonist over idle neighbour",
                       g2["ttft_p50"], g1["ttft_p50"], "s"),
        ]
        if g2["tpot_p50"] and g1["tpot_p50"]:
            change = abs(g2["tpot_p50"] / g1["tpot_p50"] - 1)
            lines.append(
                f"- The antagonist moved TPOT by {change:.1%} across the arm."
                + (" No material TPOT degradation appeared under this treatment."
                   if change < 0.05 else
                   " This treatment produced material TPOT degradation.")
            )
    lines.append("")

    lines += ["## What does a slice cost when nobody is hostile?", ""]
    if g1 and g4:
        lines += [
            ratio_line("TPOT p50, one slice over undivided card at equal rate",
                       g1["tpot_p50"], g4["tpot_p50"], "s"),
            ratio_line("TTFT p50, one slice over undivided card at equal rate",
                       g1["ttft_p50"], g4["ttft_p50"], "s"),
        ]
    if args.track_b_baseline_tpot and g4:
        lines.append(ratio_line(
            "Cross-check, g4 against the Track B undivided control",
            g4["tpot_p50"], args.track_b_baseline_tpot, "s"))
    lines.append("")

    lines += ["## The pair against the undivided card, at equal offered load", ""]
    if g3 and g4:
        lines.append(ratio_line("Aggregate throughput, two slices over undivided",
                                g3["tokens_per_s"], g4["tokens_per_s"], " tok/s"))
        lines.append(ratio_line("Per-request TPOT p50, slice over undivided",
                                g3["tpot_p50"], g4["tpot_p50"], "s"))
        if g3["kv_total"] and g4["kv_total"]:
            lost = 1 - g3["kv_total"] / g4["kv_total"]
            lines += [
                f"- KV capacity: {g3['kv_total']:,.0f} tokens across two slices "
                f"vs {g4['kv_total']:,.0f} on the undivided card, "
                f"a {lost:.1%} loss.",
                "- The loss is weight duplication: each engine holds its own copy "
                "of the model, so the pair pays for the weights twice.",
            ]
    lines.append("")

    # The break-even is the number a reader can act on: how contended must the
    # shared card be before the permanent partitioning tax is worth paying?
    if g1 and args.track_b_baseline_tpot and args.track_b_contended_tpot:
        idle = args.track_b_baseline_tpot
        contended = args.track_b_contended_tpot
        partitioned = g1["tpot_p50"]
        lines += ["## Break-even against a shared card", ""]
        lines += [
            f"- A slice costs {partitioned:.4g}s per token whether or not the "
            "neighbour is active.",
            f"- A shared card costs {idle:.4g}s idle and {contended:.4g}s under "
            "the Track B HBM antagonist.",
        ]
        if contended > idle:
            fraction = (partitioned - idle) / (contended - idle)
            if fraction <= 0:
                verdict = ("The slice beats the shared card even when the "
                           "neighbour is completely idle.")
            elif fraction >= 1:
                verdict = ("The shared card wins at every duty cycle; "
                           "partitioning never pays for itself on latency.")
            else:
                verdict = (
                    f"Partitioning wins on latency only once the neighbour is "
                    f"contending more than {fraction:.0%} of the time."
                )
            lines.append(f"- Break-even duty cycle: {verdict}")
        lines.append("")

    report = args.out_dir / "track_g_summary.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"cases analyzed: {len(cases)}")
    print(f"wrote {per_case}")
    print(f"wrote {report}")


if __name__ == "__main__":
    main()
