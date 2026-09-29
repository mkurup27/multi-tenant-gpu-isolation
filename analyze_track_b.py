#!/usr/bin/env python3
"""Reduce Track B treatments to a per-layer contention signature library.

Effects are estimated against zero-intensity controls drawn from the same
temporal block, per analysis-plan.md amendment A3. Blocks are detected from
gaps in the treatment timeline rather than hardcoded, so a future interruption
is handled the same way.

    python3 analyze_track_b.py results/track-b --out-dir analysis
"""

import argparse
import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

TAG = re.compile(r"^(\d+)_r(\d+)_(.+)_i([\d.]+)$")
BLOCK_GAP_MINUTES = 30

# Ratios are only meaningful where the control baseline is reliably non-zero.
# CPU steal and iowait sit at exactly 0.00 on an idle GPU Droplet, so those are
# reported as absolute levels instead.
LATENCY_METRICS = ("ttft_p50", "ttft_p99", "ttft_cv", "tpot_p50", "tokens_per_s")
TELEMETRY_METRICS = ("sm_clock_mhz", "mem_clock_mhz", "power_w", "gpu_util",
                     "power_capped_frac", "cpu_steal_p95", "cpu_iowait_p95",
                     "cpu_busy_median")
REPORT_RATIO = ("ttft_p50", "ttft_p99", "ttft_cv", "tpot_p50")
REPORT_ABSOLUTE = ("sm_clock_mhz", "mem_clock_mhz", "power_w", "cpu_busy_median")


def leading_number(text):
    match = re.match(r"\s*(-?[\d.]+)", text or "")
    return float(match.group(1)) if match else None


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def percentile(values, pct):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    rank = max(0, min(len(values) - 1, int(round((pct / 100) * len(values) + 0.5)) - 1))
    return values[rank]


def parse_gpu_csv(path):
    """nvidia-smi --format=csv, values carrying unit suffixes."""
    rows = []
    with path.open() as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return {}
        fields = {name.strip(): name for name in reader.fieldnames}
        for row in reader:
            rows.append({key: (row.get(orig) or "").strip()
                         for key, orig in fields.items()})
    if not rows:
        return {}

    def column(fragment):
        for key in rows[0]:
            if fragment in key:
                return key
        return None

    sm = column("sm [MHz]")
    mem = column("memory [MHz]")
    power = column("power.draw")
    util = column("utilization.gpu")
    cap = column("sw_power_cap")

    capped = None
    if cap:
        flags = [row[cap].lower() for row in rows if row.get(cap)]
        active = [flag for flag in flags if flag.startswith("active")]
        capped = len(active) / len(flags) if flags else None

    return {
        "samples": len(rows),
        "sm_clock_mhz": median([leading_number(row.get(sm)) for row in rows]) if sm else None,
        "mem_clock_mhz": median([leading_number(row.get(mem)) for row in rows]) if mem else None,
        "power_w": median([leading_number(row.get(power)) for row in rows]) if power else None,
        "gpu_util": median([leading_number(row.get(util)) for row in rows]) if util else None,
        "power_capped_frac": capped,
    }


def parse_mpstat(path):
    """mpstat -P ALL 1 emits fixed-width text; locate columns from its header."""
    steal, iowait, idle = [], [], []
    columns = None
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if "%steal" in fields:
            columns = {name: index for index, name in enumerate(fields)}
            continue
        if not columns or "all" not in fields:
            continue
        cpu_index = fields.index("all")
        offset = cpu_index - columns.get("CPU", cpu_index)
        try:
            steal.append(float(fields[columns["%steal"] + offset]))
            iowait.append(float(fields[columns["%iowait"] + offset]))
            idle.append(float(fields[columns["%idle"] + offset]))
        except (IndexError, ValueError, KeyError):
            continue
    if not steal:
        return {}
    return {
        "cpu_steal_p95": percentile(steal, 95),
        "cpu_iowait_p95": percentile(iowait, 95),
        "cpu_busy_median": 100 - median(idle) if idle else None,
    }


def load_treatment(directory):
    match = TAG.match(directory.name)
    if not match:
        return None
    summary_path = directory / "victim.summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text())

    started = None
    meta_path = directory / "treatment.meta"
    if meta_path.is_file():
        for line in meta_path.read_text().splitlines():
            if line.startswith("started_utc="):
                started = datetime.strptime(line.split("=", 1)[1],
                                            "%Y-%m-%dT%H:%M:%SZ")

    record = {
        "sequence": int(match.group(1)),
        "repeat": int(match.group(2)),
        "layer": match.group(3),
        "intensity": float(match.group(4)),
        "started_utc": started,
        "ttft_p50": summary["ttft_s"]["p50"],
        "ttft_p99": summary["ttft_s"]["p99"],
        "ttft_cv": summary["ttft_s"]["cv"],
        "tpot_p50": summary["tpot_s"]["p50"],
        "tokens_per_s": summary["throughput"]["tokens_per_completion_span_s"],
        "completed_ok": summary["counts"]["completed_ok"],
        "request_errors": summary["counts"]["request_errors"],
        "dropped_admissions": summary["counts"]["dropped_admissions"],
    }

    for pattern, parser in ((".gpu.csv", parse_gpu_csv), (".cpu.csv", parse_mpstat)):
        for path in directory.glob(f"*{pattern}"):
            try:
                record.update(parser(path))
            except Exception as error:  # a malformed capture must not sink the run
                print(f"warning: {path.name}: {error}", file=sys.stderr)
            break
    return record


def assign_blocks(records):
    ordered = sorted(records, key=lambda r: r["sequence"])
    block = 1
    previous = None
    for record in ordered:
        start = record["started_utc"]
        if previous and start and (start - previous).total_seconds() > BLOCK_GAP_MINUTES * 60:
            block += 1
        record["block"] = block
        previous = start or previous
    return ordered


def block_baselines(records):
    baselines = {}
    for record in records:
        if record["layer"] == "control":
            baselines.setdefault(record["block"], []).append(record)
    return {
        block: {metric: median([r.get(metric) for r in controls])
                for metric in LATENCY_METRICS + TELEMETRY_METRICS}
        for block, controls in baselines.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    records = [r for r in (load_treatment(d) for d in sorted(args.results.iterdir())
                           if d.is_dir()) if r]
    if not records:
        parser.error(f"no readable treatments under {args.results}")

    records = assign_blocks(records)
    baselines = block_baselines(records)
    missing = sorted({r["block"] for r in records} - set(baselines))
    if missing:
        print(f"warning: blocks without controls, effects omitted: {missing}",
              file=sys.stderr)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    metrics = LATENCY_METRICS + TELEMETRY_METRICS

    per_treatment = args.out_dir / "track_b_effects.csv"
    with per_treatment.open("w", newline="") as handle:
        columns = (["sequence", "repeat", "block", "layer", "intensity",
                    "completed_ok", "request_errors", "dropped_admissions"]
                   + list(metrics) + [f"{m}_ratio" for m in metrics])
        writer = csv.DictWriter(handle, fieldnames=columns,
                                extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for record in records:
            row = dict(record)
            base = baselines.get(record["block"], {})
            for metric in metrics:
                value, reference = record.get(metric), base.get(metric)
                row[f"{metric}_ratio"] = (
                    round(value / reference, 4)
                    if value is not None and reference else None
                )
            writer.writerow(row)

    grouped = {}
    for record in records:
        if record["layer"] == "control":
            continue
        grouped.setdefault((record["layer"], record["intensity"]), []).append(record)

    def ratio_cell(members, metric):
        ratios = []
        for record in members:
            reference = baselines.get(record["block"], {}).get(metric)
            value = record.get(metric)
            if value is not None and reference:
                ratios.append(value / reference)
        centre = median(ratios)
        return f"{centre:.2f}" if centre is not None else "-"

    def absolute_cell(members, metric):
        centre = median([r.get(metric) for r in members])
        return f"{centre:.0f}" if centre is not None else "-"

    lines = ["# Track B contention signature library", ""]

    for block, base in sorted(baselines.items()):
        members = [r for r in records if r["block"] == block]
        controls = [r for r in members if r["layer"] == "control"]
        span = [r["started_utc"] for r in members if r["started_utc"]]
        window = (f"{min(span):%Y-%m-%dT%H:%MZ} to {max(span):%Y-%m-%dT%H:%MZ}"
                  if span else "unknown")
        lines += [f"Block {block}: {len(members)} treatments, {len(controls)} controls, "
                  f"{window}. Baseline TTFT p99 {base['ttft_p99']:.4f}s, "
                  f"TPOT p50 {base['tpot_p50']:.5f}s, "
                  f"throughput {base['tokens_per_s']:.1f} tok/s."]
    lines += [""]

    lines += ["## Tenant-visible latency, as a ratio to the same-block control", "",
              "Above 1.00 means the treatment made that quantity worse.", ""]
    header = ["layer", "intensity", "n"] + list(REPORT_RATIO)
    lines += ["| " + " | ".join(header) + " |",
              "|" + "|".join(["---"] * len(header)) + "|"]
    for (layer, intensity), members in sorted(grouped.items()):
        cells = [layer, f"{intensity}", str(len(members))]
        cells += [ratio_cell(members, metric) for metric in REPORT_RATIO]
        lines += ["| " + " | ".join(cells) + " |"]

    lines += ["", "## Telemetry, as absolute medians", "",
              "Absolute rather than relative because several of these have a "
              "control baseline of exactly zero.", ""]
    header = ["layer", "intensity", "n"] + list(REPORT_ABSOLUTE)
    lines += ["| " + " | ".join(header) + " |",
              "|" + "|".join(["---"] * len(header)) + "|"]
    controls = [r for r in records if r["layer"] == "control"]
    lines += ["| " + " | ".join(["control", "0.0", str(len(controls))]
                                + [absolute_cell(controls, m)
                                   for m in REPORT_ABSOLUTE]) + " |"]
    for (layer, intensity), members in sorted(grouped.items()):
        cells = [layer, f"{intensity}", str(len(members))]
        cells += [absolute_cell(members, metric) for metric in REPORT_ABSOLUTE]
        lines += ["| " + " | ".join(cells) + " |"]

    lines += ["", "## Quantities that did not respond to any treatment", ""]
    for metric in metrics:
        values = [r.get(metric) for r in records if r.get(metric) is not None]
        if not values:
            lines += [f"- {metric}: never captured"]
            continue
        low, high = min(values), max(values)
        if high == 0:
            lines += [f"- {metric}: exactly 0 in all {len(values)} treatments"]
        elif low and high / low < 1.05:
            lines += [f"- {metric}: {low:.4g} to {high:.4g}, "
                      f"a spread under 5% across all treatments"]

    signatures = args.out_dir / "track_b_signatures.md"
    signatures.write_text("\n".join(lines) + "\n")

    print(f"treatments analyzed: {len(records)}")
    print(f"blocks detected:     {sorted({r['block'] for r in records})}")
    print(f"wrote {per_treatment}")
    print(f"wrote {signatures}")


if __name__ == "__main__":
    main()
