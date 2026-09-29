#!/usr/bin/env python3
"""Validate Track C completeness before teardown or analysis."""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--start-utc", required=True, help="ISO-8601, e.g. 2026-09-05T00:00:00Z")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--interval-minutes", type=int, default=30)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start_utc.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(days=args.days)
    expected = args.days * 24 * 60 // args.interval_minutes

    run_dirs = []
    for path in sorted(candidate for candidate in args.results.iterdir() if candidate.is_dir()):
        try:
            timestamp = datetime.strptime(path.name, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if start <= timestamp < end:
            run_dirs.append(path)
    complete = [path for path in run_dirs if (path / "COMPLETE").exists()]
    failures = []
    tiers = Counter()

    for run_dir in complete:
        summaries = list(run_dir.glob("*.summary.json"))
        if not summaries:
            failures.append((run_dir.name, "no summary"))
            continue
        if not list(run_dir.glob("*.gpu.csv")):
            failures.append((run_dir.name, "no GPU telemetry"))
        if not list(run_dir.glob("*.cpu.csv")):
            failures.append((run_dir.name, "no CPU telemetry"))
        for summary_path in summaries:
            try:
                summary = json.loads(summary_path.read_text())
            except Exception as exc:
                failures.append((run_dir.name, f"invalid summary: {exc}"))
                continue
            tier = summary.get("tier", "missing")
            tiers[tier] += 1
            counts = summary.get("counts", {})
            if counts.get("completed_ok", 0) == 0:
                failures.append((run_dir.name, f"{tier}: zero completed requests"))
            if counts.get("dropped_admissions", 0) > 0:
                failures.append((run_dir.name, f"{tier}: client admission drops"))
            stem = summary_path.name.removesuffix(".summary.json")
            raw = run_dir / f"{stem}.jsonl"
            metrics = run_dir / f"{stem}.metrics.jsonl"
            if not raw.exists() or raw.stat().st_size == 0:
                failures.append((run_dir.name, f"{tier}: missing raw JSONL"))
            if tier == "gpu-droplet" and (not metrics.exists() or metrics.stat().st_size == 0):
                failures.append((run_dir.name, f"{tier}: missing vLLM metrics"))

    print(f"expected scheduled windows: {expected}")
    print(f"run directories:           {len(run_dirs)}")
    print(f"complete windows:          {len(complete)}")
    for tier, count in sorted(tiers.items()):
        print(f"summary count {tier:20s} {count}")
    if failures:
        print("\nVALIDATION FAILURES:")
        for run_id, reason in failures:
            print(f"  {run_id}: {reason}")
        sys.exit(1)
    if len(complete) < expected:
        print(f"\nINCOMPLETE: {expected - len(complete)} scheduled windows are missing")
        sys.exit(1)
    print("\nTrack C files pass structural validation.")


if __name__ == "__main__":
    main()
