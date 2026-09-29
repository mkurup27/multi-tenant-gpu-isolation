#!/usr/bin/env python3
"""Reduce the validity-checked MPS arm to baseline and treatment effects.

    python3 analyze_track_mps.py results/track-mps --out-dir analysis
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

KINDS = (
    "m1_undivided_solo",
    "m2_time_sharing",
    "m3_mps",
)
LABELS = {
    "m1_undivided_solo": "undivided GPU, victim alone",
    "m2_time_sharing": "normal time-sharing with HBM antagonist",
    "m3_mps": "MPS at 50/50 active-thread limits with HBM antagonist",
}


def median(values):
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def span(values):
    values = [value for value in values if value is not None]
    return (min(values), max(values)) if values else (None, None)


def load_case(directory):
    summary_path = directory / "victim.summary.json"
    if not summary_path.is_file():
        return None

    summary = json.loads(summary_path.read_text())
    meta = {}
    meta_path = directory / "case.meta"
    if meta_path.is_file():
        for line in meta_path.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                meta[key.strip()] = value.strip()

    kind = meta.get("case", directory.name.rsplit("_r", 1)[0])
    validation_path = directory / "mps-clients.txt"
    validation_text = (
        validation_path.read_text() if validation_path.is_file() else ""
    )
    counts = summary.get("counts", {})
    return {
        "tag": directory.name,
        "kind": kind,
        "repeat": meta.get("repeat"),
        "completed_ok": counts.get("completed_ok"),
        "request_errors": counts.get("request_errors"),
        "dropped_admissions": counts.get("dropped_admissions"),
        "ttft_p50": summary.get("ttft_s", {}).get("p50"),
        "ttft_p99": summary.get("ttft_s", {}).get("p99"),
        "tpot_p50": summary.get("tpot_s", {}).get("p50"),
        "tokens_per_s": summary.get("throughput", {}).get(
            "tokens_per_completion_span_s"
        ),
        "mps_clients_validated": (
            "MPS client validation passed" in validation_text
            if kind == "m3_mps"
            else None
        ),
    }


def fmt(value, spec=".5f"):
    return format(value, spec) if isinstance(value, (int, float)) else "-"


def ratio(label, numerator, denominator):
    if not numerator or not denominator:
        return f"- {label}: insufficient data"
    return f"- {label}: {numerator / denominator:.2f}x"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    if not args.results.is_dir():
        parser.error(f"{args.results} does not exist; transfer MPS results first")

    directories = [path for path in sorted(args.results.iterdir()) if path.is_dir()]
    cases = [case for case in (load_case(path) for path in directories) if case]
    if not cases:
        parser.error(f"no readable cases under {args.results}")

    incomplete = [
        path.name for path in directories if not (path / "COMPLETE").is_file()
    ]
    if incomplete:
        print(f"warning: cases without COMPLETE: {incomplete}", file=sys.stderr)

    invalid_mps = [
        case["tag"]
        for case in cases
        if case["kind"] == "m3_mps" and not case["mps_clients_validated"]
    ]
    if invalid_mps:
        print(
            "invalid MPS cases without two-client proof: " + ", ".join(invalid_mps),
            file=sys.stderr,
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "track_mps_cases.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(cases[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(cases)

    grouped = {
        kind: [case for case in cases if case["kind"] == kind] for kind in KINDS
    }
    medians = {
        kind: {
            metric: median([case[metric] for case in arm])
            for metric in ("ttft_p50", "ttft_p99", "tpot_p50", "tokens_per_s")
        }
        for kind, arm in grouped.items()
        if arm
    }

    lines = [
        "# Track G: MPS scheduling",
        "",
        f"Cases analysed: {len(cases)} of {len(directories)} directories. "
        f"Request errors: {sum(case['request_errors'] or 0 for case in cases)}. "
        f"Client admission drops: "
        f"{sum(case['dropped_admissions'] or 0 for case in cases)}.",
        "",
        f"MPS cases with two-client validation: "
        f"{sum(case['mps_clients_validated'] is True for case in cases)} of "
        f"{sum(case['kind'] == 'm3_mps' for case in cases)}.",
        "",
    ]
    if incomplete:
        lines += [f"Cases missing COMPLETE: {', '.join(incomplete)}.", ""]
    if invalid_mps:
        lines += [
            f"Invalid MPS cases excluded from interpretation: "
            f"{', '.join(invalid_mps)}.",
            "",
        ]

    lines += [
        "## Per-configuration medians",
        "",
        "| configuration | repeats | ok | errors | TTFT p50 | TTFT p99 | "
        "TPOT p50 | TPOT range | tok/s |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind in KINDS:
        arm = grouped[kind]
        if not arm:
            continue
        tpot_low, tpot_high = span([case["tpot_p50"] for case in arm])
        item = medians[kind]
        lines.append(
            "| "
            + " | ".join(
                [
                    f"{LABELS[kind]} (`{kind}`)",
                    str(len(arm)),
                    str(sum(case["completed_ok"] or 0 for case in arm)),
                    str(sum(case["request_errors"] or 0 for case in arm)),
                    fmt(item["ttft_p50"]),
                    fmt(item["ttft_p99"]),
                    fmt(item["tpot_p50"]),
                    f"{fmt(tpot_low)}-{fmt(tpot_high)}",
                    fmt(item["tokens_per_s"], ".1f"),
                ]
            )
            + " |"
        )

    baseline = medians.get("m1_undivided_solo")
    sharing = medians.get("m2_time_sharing")
    mps = medians.get("m3_mps")
    lines += ["", "## Treatment effects", ""]
    if baseline and sharing:
        lines += [
            ratio(
                "Time-sharing TPOT over victim-alone baseline",
                sharing["tpot_p50"],
                baseline["tpot_p50"],
            ),
            ratio(
                "Time-sharing TTFT p99 over victim-alone baseline",
                sharing["ttft_p99"],
                baseline["ttft_p99"],
            ),
        ]
    if baseline and mps and not invalid_mps:
        lines += [
            ratio(
                "MPS TPOT over victim-alone baseline",
                mps["tpot_p50"],
                baseline["tpot_p50"],
            ),
            ratio(
                "MPS TTFT p99 over victim-alone baseline",
                mps["ttft_p99"],
                baseline["ttft_p99"],
            ),
            ratio(
                "MPS throughput over victim-alone baseline",
                mps["tokens_per_s"],
                baseline["tokens_per_s"],
            ),
        ]
    if sharing and mps and not invalid_mps:
        lines += [
            ratio(
                "MPS TPOT over normal time-sharing",
                mps["tpot_p50"],
                sharing["tpot_p50"],
            ),
            ratio(
                "MPS TTFT p99 over normal time-sharing",
                mps["ttft_p99"],
                sharing["ttft_p99"],
            ),
        ]
        lines += [
            "",
            "The MPS arm applies a 50% active-thread limit to both clients. "
            "That limits executable threads; it does not create MIG-style "
            "dedicated memory bandwidth. Interpret the result as this exact "
            "MPS policy under an HBM antagonist, not as a universal MPS effect.",
        ]

    report_path = args.out_dir / "track_mps_summary.md"
    report_path.write_text("\n".join(lines) + "\n")
    print(f"cases analyzed: {len(cases)}")
    print(f"wrote {csv_path}")
    print(f"wrote {report_path}")


if __name__ == "__main__":
    main()
