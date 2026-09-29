#!/usr/bin/env python3
"""Analyze seven-day Track C at the scheduled-window level.

Requests estimate one window; they are never treated as independent replicates.
Uncertainty intervals resample the seven 24-hour study-day blocks.

    python3 analyze_track_c.py \
      track-c-extracted/var/lib/isolation/results/track-c \
      --start-utc 2026-09-07T05:00:00Z --days 7 --out-dir analysis
"""

import argparse
import csv
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINTS = {
    "ttft_p99": ("TTFT p99", "s"),
    "ttft_p50": ("TTFT p50", "s"),
    "ttft_cv": ("TTFT coefficient of variation", ""),
    "e2e_p99": ("end-to-end p99", "s"),
    "tpot_p50": ("TPOT p50", "s"),
    "tokens_per_s": ("output-token throughput", "tokens/s"),
}

TELEMETRY_LABELS = {
    "gpu_sm_clock_median": "GPU SM clock median (MHz)",
    "gpu_power_median": "GPU power median (W)",
    "gpu_temp_p95": "GPU temperature p95 (C)",
    "gpu_util_median": "GPU utilization median (%)",
    "gpu_memory_util_median": "GPU memory utilization median (%)",
    "cpu_steal_p95": "CPU steal p95 (%)",
    "cpu_iowait_p95": "CPU iowait p95 (%)",
    "disk_vda_util_p95": "root-disk utilization p95 (%)",
    "net_eth1_rx_mbps": "VPC receive rate (Mbit/s)",
    "net_eth1_tx_mbps": "VPC transmit rate (Mbit/s)",
    "waiting_max": "vLLM waiting requests maximum",
    "running_max": "vLLM running requests maximum",
    "kv_usage_max": "vLLM KV-cache usage maximum",
}

NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)")


def percentile(values, probability):
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def numeric(value):
    if value is None:
        return None
    match = NUMBER.search(str(value))
    return float(match.group()) if match else None


def measured_samples(values, warmup_seconds, measurement_seconds):
    """Approximate the measured interval in a one-Hz host telemetry series."""
    start = int(round(warmup_seconds))
    stop = start + int(round(measurement_seconds))
    return values[start:stop]


def read_gpu(path, warmup_seconds, measurement_seconds):
    if not path.is_file():
        return {}
    rows = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append({key.strip(): value.strip() for key, value in row.items()})
    rows = measured_samples(rows, warmup_seconds, measurement_seconds)
    if not rows:
        return {}

    def values(fragment):
        key = next((key for key in rows[0] if fragment in key), None)
        return [numeric(row.get(key)) for row in rows] if key else []

    def active_fraction(fragment):
        key = next((key for key in rows[0] if fragment in key), None)
        if not key:
            return None
        observed = [row.get(key) for row in rows if row.get(key)]
        return (
            sum(value.lower() == "active" for value in observed) / len(observed)
            if observed
            else None
        )

    return {
        "gpu_sm_clock_median": percentile(values("clocks.current.sm"), 0.5),
        "gpu_memory_clock_median": percentile(
            values("clocks.current.memory"), 0.5
        ),
        "gpu_power_median": percentile(values("power.draw"), 0.5),
        "gpu_power_p95": percentile(values("power.draw"), 0.95),
        "gpu_temp_p95": percentile(values("temperature.gpu"), 0.95),
        "gpu_util_median": percentile(values("utilization.gpu"), 0.5),
        "gpu_memory_util_median": percentile(
            values("utilization.memory"), 0.5
        ),
        "gpu_power_capped_fraction": active_fraction("sw_power_cap"),
        "gpu_thermal_slowdown_fraction": max(
            (
                value
                for value in (
                    active_fraction("hw_thermal_slowdown"),
                    active_fraction("sw_thermal_slowdown"),
                    active_fraction("hw_slowdown"),
                )
                if value is not None
            ),
            default=None,
        ),
    }


def read_cpu(path, warmup_seconds, measurement_seconds):
    steal = []
    iowait = []
    if not path.is_file():
        return {}
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 12 and fields[1] == "all":
            try:
                iowait.append(float(fields[5]))
                steal.append(float(fields[8]))
            except ValueError:
                continue
    steal = measured_samples(steal, warmup_seconds, measurement_seconds)
    iowait = measured_samples(iowait, warmup_seconds, measurement_seconds)
    return {
        "cpu_steal_p95": percentile(steal, 0.95),
        "cpu_steal_max": max(steal) if steal else None,
        "cpu_iowait_p95": percentile(iowait, 0.95),
        "cpu_iowait_max": max(iowait) if iowait else None,
    }


def read_disk(path, warmup_seconds, measurement_seconds):
    """Read vda utilization, excluding iostat's first since-boot report."""
    if not path.is_file():
        return {}
    report = 0
    utilization = []
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if fields and fields[0] == "Device":
            report += 1
            continue
        if report >= 2 and fields and fields[0] == "vda":
            try:
                utilization.append(float(fields[-1]))
            except ValueError:
                continue
    utilization = measured_samples(
        utilization, warmup_seconds, measurement_seconds
    )
    return {
        "disk_vda_util_p95": percentile(utilization, 0.95),
        "disk_vda_util_max": max(utilization) if utilization else None,
    }


def read_network(
    path, warmup_seconds, measurement_seconds, interface="eth1"
):
    if not path.is_file():
        return {}
    samples = []
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 18 or fields[1] != interface:
            continue
        try:
            timestamp = datetime.fromisoformat(fields[0].replace("Z", "+00:00"))
            samples.append((timestamp, int(fields[2]), int(fields[10])))
        except (ValueError, IndexError):
            continue
    start = int(round(warmup_seconds))
    stop = start + int(round(measurement_seconds)) + 1
    samples = samples[start:stop]
    if len(samples) < 2:
        return {}
    elapsed = (samples[-1][0] - samples[0][0]).total_seconds()
    if elapsed <= 0:
        return {}
    return {
        "net_eth1_rx_mbps": (
            (samples[-1][1] - samples[0][1]) * 8 / elapsed / 1_000_000
        ),
        "net_eth1_tx_mbps": (
            (samples[-1][2] - samples[0][2]) * 8 / elapsed / 1_000_000
        ),
    }


def read_server_metrics(path):
    waiting = []
    running = []
    kv_usage = []
    preemptions = []
    successful = 0
    if not path.is_file():
        return {}
    with path.open() as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("error"):
                continue
            successful += 1
            for name, value in record.get("values", {}).items():
                if "num_requests_waiting{" in name:
                    waiting.append(value)
                elif "num_requests_running{" in name:
                    running.append(value)
                elif "kv_cache_usage_perc" in name:
                    kv_usage.append(value)
                elif "num_preemptions_total" in name:
                    preemptions.append(value)
    return {
        "metrics_samples": successful,
        "waiting_max": max(waiting) if waiting else None,
        "running_max": max(running) if running else None,
        "kv_usage_max": max(kv_usage) if kv_usage else None,
        "preemptions_delta": (
            max(preemptions) - min(preemptions) if preemptions else None
        ),
    }


def parse_run_timestamp(name):
    return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def read_metadata(path):
    values = {}
    if path.is_file():
        for line in path.read_text(errors="replace").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def parse_iso_timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_window(directory, start, interval):
    summary_path = directory / "gpu-droplet.summary.json"
    if not summary_path.is_file():
        return None
    summary = json.loads(summary_path.read_text())
    actual = parse_run_timestamp(directory.name)
    raw_index = (actual - start).total_seconds() / interval.total_seconds()
    scheduled_index = round(raw_index)
    scheduled = start + scheduled_index * interval
    drift = (actual - scheduled).total_seconds()
    window_meta = read_metadata(directory / "window.meta")
    controller_start = parse_iso_timestamp(window_meta.get("started_utc"))

    counts = summary.get("counts", {})
    timing = summary.get("timing", {})
    config = summary.get("config", {})
    warmup_seconds = config.get("warmup_admission_duration_s", 30)
    measurement_seconds = config.get("measurement_admission_duration_s", 120)
    prefix = directory / directory.name
    telemetry_meta = read_metadata(prefix.with_suffix(".telemetry.meta"))
    telemetry_start = parse_iso_timestamp(telemetry_meta.get("started_utc"))
    row = {
        "run_id": directory.name,
        "actual_utc": actual.isoformat().replace("+00:00", "Z"),
        "scheduled_utc": scheduled.isoformat().replace("+00:00", "Z"),
        "directory_drift_s": drift,
        "controller_start_utc": (
            controller_start.isoformat().replace("+00:00", "Z")
            if controller_start
            else None
        ),
        "controller_start_drift_s": (
            (controller_start - scheduled).total_seconds()
            if controller_start
            else None
        ),
        "telemetry_start_utc": (
            telemetry_start.isoformat().replace("+00:00", "Z")
            if telemetry_start
            else None
        ),
        "telemetry_start_drift_s": (
            (telemetry_start - scheduled).total_seconds()
            if telemetry_start
            else None
        ),
        "study_day": scheduled_index // 48 + 1,
        "utc_hour": scheduled.hour,
        "completed_ok": counts.get("completed_ok"),
        "request_errors": counts.get("request_errors"),
        "dropped_admissions": counts.get("dropped_admissions"),
        "output_tokens": counts.get("output_tokens"),
        "completion_span_s": timing.get("completion_span_s"),
        "ttft_p50": summary.get("ttft_s", {}).get("p50"),
        "ttft_p99": summary.get("ttft_s", {}).get("p99"),
        "ttft_cv": summary.get("ttft_s", {}).get("cv"),
        "e2e_p99": summary.get("e2e_s", {}).get("p99"),
        "tpot_p50": summary.get("tpot_s", {}).get("p50"),
        "tokens_per_s": summary.get("throughput", {}).get(
            "tokens_per_completion_span_s"
        ),
    }
    row.update(
        read_gpu(
            prefix.with_suffix(".gpu.csv"), warmup_seconds, measurement_seconds
        )
    )
    row.update(
        read_cpu(
            prefix.with_suffix(".cpu.csv"), warmup_seconds, measurement_seconds
        )
    )
    row.update(
        read_disk(
            prefix.with_suffix(".disk.csv"), warmup_seconds, measurement_seconds
        )
    )
    row.update(
        read_network(
            prefix.with_suffix(".net.csv"), warmup_seconds, measurement_seconds
        )
    )
    row.update(read_server_metrics(directory / "gpu-droplet.metrics.jsonl"))
    dcgm_path = prefix.with_suffix(".dcgm.csv")
    row["dcgm_valid"] = (
        dcgm_path.is_file()
        and "unable to establish a connection"
        not in dcgm_path.read_text(errors="replace").lower()
    )
    return row


def day_block_bootstrap(rows, metric, probability, repetitions, seed):
    by_day = defaultdict(list)
    for row in rows:
        value = row.get(metric)
        if value is not None:
            by_day[row["study_day"]].append(value)
    days = sorted(by_day)
    if not days:
        return (None, None)
    rng = random.Random(seed)
    estimates = []
    for _ in range(repetitions):
        sample = []
        for _ in days:
            sample.extend(by_day[rng.choice(days)])
        estimates.append(percentile(sample, probability))
    return (percentile(estimates, 0.025), percentile(estimates, 0.975))


def rank(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranked = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        average = (position + end - 1) / 2 + 1
        for index in order[position:end]:
            ranked[index] = average
        position = end
    return ranked


def pearson(left, right):
    if len(left) < 3:
        return None
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else None


def spearman(rows, metric):
    pairs = [
        (row["ttft_p99"], row.get(metric))
        for row in rows
        if row.get("ttft_p99") is not None and row.get(metric) is not None
    ]
    if len(pairs) < 3:
        return None
    left, right = zip(*pairs)
    return pearson(rank(list(left)), rank(list(right)))


def endpoint_distributions(rows, repetitions):
    output = {}
    for metric in ENDPOINTS:
        values = [row[metric] for row in rows if row.get(metric) is not None]
        output[metric] = {}
        for label, probability in (
            ("median", 0.5),
            ("p90", 0.9),
            ("p95", 0.95),
            ("p99", 0.99),
        ):
            low, high = day_block_bootstrap(
                rows,
                metric,
                probability,
                repetitions,
                20260907 + int(probability * 100) + len(metric),
            )
            output[metric][label] = {
                "estimate": percentile(values, probability),
                "ci95_low": low,
                "ci95_high": high,
            }
    return output


def daily_summary(rows):
    result = []
    for day in sorted({row["study_day"] for row in rows}):
        group = [row for row in rows if row["study_day"] == day]
        result.append(
            {
                "study_day": day,
                "start_utc": min(row["scheduled_utc"] for row in group),
                "windows": len(group),
                "ttft_p99_median": percentile(
                    [row["ttft_p99"] for row in group], 0.5
                ),
                "ttft_p99_p95": percentile(
                    [row["ttft_p99"] for row in group], 0.95
                ),
                "tpot_p50_median": percentile(
                    [row["tpot_p50"] for row in group], 0.5
                ),
                "tokens_per_s_median": percentile(
                    [row["tokens_per_s"] for row in group], 0.5
                ),
            }
        )
    return result


def hourly_summary(rows):
    overall = percentile([row["ttft_p99"] for row in rows], 0.5)
    result = []
    for hour in range(24):
        group = [row for row in rows if row["utc_hour"] == hour]
        by_day = []
        for day in sorted({row["study_day"] for row in group}):
            values = [
                row["ttft_p99"] for row in group if row["study_day"] == day
            ]
            by_day.append(percentile(values, 0.5))
        estimate = percentile(by_day, 0.5)
        result.append(
            {
                "utc_hour": hour,
                "windows": len(group),
                "median_of_daily_hour_medians": estimate,
                "ratio_to_overall_median": (
                    estimate / overall if estimate is not None and overall else None
                ),
                "daily_min": min(by_day) if by_day else None,
                "daily_max": max(by_day) if by_day else None,
            }
        )
    return result


def telemetry_associations(rows):
    cutoff = percentile([row["ttft_p99"] for row in rows], 0.9)
    slow = [row for row in rows if row["ttft_p99"] >= cutoff]
    rest = [row for row in rows if row["ttft_p99"] < cutoff]
    result = []
    for metric, label in TELEMETRY_LABELS.items():
        slow_median = percentile(
            [row.get(metric) for row in slow if row.get(metric) is not None], 0.5
        )
        rest_median = percentile(
            [row.get(metric) for row in rest if row.get(metric) is not None], 0.5
        )
        result.append(
            {
                "metric": metric,
                "label": label,
                "slowest_decile_median": slow_median,
                "other_windows_median": rest_median,
                "ratio": (
                    slow_median / rest_median
                    if slow_median is not None and rest_median
                    else None
                ),
                "spearman_rho": spearman(rows, metric),
            }
        )
    return result


def fmt(value, digits=4):
    return f"{value:.{digits}g}" if isinstance(value, (int, float)) else "-"


def fmt_endpoint(metric, value):
    if not isinstance(value, (int, float)):
        return "-"
    if metric == "tokens_per_s":
        return f"{value:.2f}"
    return f"{value:.4g}"


def write_markdown(report, path):
    overview = report["overview"]
    distributions = report["endpoint_distributions"]
    lines = [
        "# Track C: seven-day longitudinal result",
        "",
        f"Included windows: {overview['included_windows']} of "
        f"{overview['expected_windows']}. Excluded: {overview['excluded_windows']}. "
        f"Request errors: {overview['request_errors']}. Admission drops: "
        f"{overview['dropped_admissions']}.",
        f"Request-error rate: {overview['request_error_rate']:.1%} "
        f"({overview['request_errors']}/{overview['request_attempts']}). "
        f"Admission-drop rate: {overview['admission_drop_rate']:.1%} "
        f"({overview['dropped_admissions']}/{overview['admission_attempts']}).",
        "",
        "Intervals below are 95% day-block bootstrap intervals over seven "
        "24-hour study days. With seven blocks, tail intervals are imprecise.",
        "",
        "## Window-level endpoint distributions",
        "",
        "| endpoint | median | p90 | p95 | p99 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for metric, (label, unit) in ENDPOINTS.items():
        cells = []
        for quantile in ("median", "p90", "p95", "p99"):
            item = distributions[metric][quantile]
            cells.append(
                f"{fmt_endpoint(metric, item['estimate'])} "
                f"[{fmt_endpoint(metric, item['ci95_low'])}, "
                f"{fmt_endpoint(metric, item['ci95_high'])}]"
            )
        lines.append(
            f"| {label}{f' ({unit})' if unit else ''} | "
            + " | ".join(cells)
            + " |"
        )

    lines += [
        "",
        "## Daily primary endpoint",
        "",
        "| study day | start UTC | windows | TTFT p99 median | TTFT p99 p95 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["daily"]:
        lines.append(
            f"| {item['study_day']} | {item['start_utc']} | {item['windows']} | "
            f"{fmt(item['ttft_p99_median'])} | {fmt(item['ttft_p99_p95'])} |"
        )

    lines += [
        "",
        "## Descriptive UTC-hour effects",
        "",
        "| UTC hour | windows | median daily-hour TTFT p99 | overall ratio | "
        "daily range |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in report["hourly"]:
        lines.append(
            f"| {item['utc_hour']:02d}:00 | {item['windows']} | "
            f"{fmt(item['median_of_daily_hour_medians'])} | "
            f"{fmt(item['ratio_to_overall_median'])} | "
            f"{fmt(item['daily_min'])}-{fmt(item['daily_max'])} |"
        )
    lines += [
        "",
        "Hour effects are unadjusted and descriptive; no set of 24 significance "
        "tests was run. Reporting every hour avoids selecting only elevated periods.",
        "",
        "## Slowest windows",
        "",
        "| scheduled UTC | TTFT p99 | TTFT p50 | TPOT p50 | tok/s | "
        "SM clock | power | CPU steal p95 | waiting max |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["slowest_windows"]:
        lines.append(
            f"| {row['scheduled_utc']} | {fmt(row['ttft_p99'])} | "
            f"{fmt(row['ttft_p50'])} | {fmt(row['tpot_p50'])} | "
            f"{fmt(row['tokens_per_s'])} | {fmt(row.get('gpu_sm_clock_median'))} | "
            f"{fmt(row.get('gpu_power_median'))} | "
            f"{fmt(row.get('cpu_steal_p95'))} | "
            f"{fmt(row.get('waiting_max'))} |"
        )

    associations = sorted(
        report["telemetry_associations"],
        key=lambda item: abs(item["spearman_rho"] or 0),
        reverse=True,
    )
    lines += [
        "",
        "## Measured-interval telemetry alongside the slowest decile",
        "",
        "| signal | slowest decile median | other windows median | ratio | "
        "Spearman rho |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in associations:
        lines.append(
            f"| {item['label']} | {fmt(item['slowest_decile_median'])} | "
            f"{fmt(item['other_windows_median'])} | {fmt(item['ratio'])} | "
            f"{fmt(item['spearman_rho'])} |"
        )
    lines += [
        "",
        "Host telemetry is restricted by sample offset to the 120-second measured "
        "admission interval after the 30-second warm-up. Associations are unadjusted "
        "descriptive comparisons across serially correlated windows. Running-request "
        "and KV-cache maxima describe realized workload and may be consequences of "
        "slower requests.",
    ]

    quality = report["quality"]
    lines += [
        "",
        "## Data quality",
        "",
        f"- Directory timestamps more than 0.5 seconds from their assigned block: "
        f"{quality['drifted_directory_timestamps']}. Maximum drift: "
        f"{fmt(quality['maximum_directory_drift_s'])} seconds.",
        f"- Controller starts more than 0.5 seconds late: "
        f"{quality['drifted_controller_starts']}. Maximum drift: "
        f"{fmt(quality['maximum_controller_start_drift_s'])} seconds.",
        f"- Host telemetry starts more than 0.5 seconds late: "
        f"{quality['drifted_telemetry_starts']}. Maximum drift: "
        f"{fmt(quality['maximum_telemetry_start_drift_s'])} seconds.",
        f"- Windows with valid DCGM samples: {quality['valid_dcgm_windows']} of "
        f"{overview['included_windows']}. DCGM was optional and is not used above.",
        f"- Windows with fewer than 120 successful vLLM metric samples: "
        f"{quality['short_metrics_windows']}.",
        f"- Windows with any waiting request: {quality['windows_with_waiting']}. "
        f"Total preemptions: {fmt(quality['total_preemptions'])}.",
        f"- Maximum one-second aggregate CPU-steal sample: "
        f"{fmt(quality['maximum_cpu_steal_sample'])}%. Window-level p95 was "
        "zero in both the slowest decile and other windows.",
        f"- Power-cap or thermal-slowdown samples: "
        f"{quality['windows_with_gpu_throttle_signal']} windows with any active flag.",
    ]
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--start-utc", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--interval-minutes", type=int, default=30)
    parser.add_argument("--bootstrap-repetitions", type=int, default=5000)
    parser.add_argument("--out-dir", type=Path, default=Path("analysis"))
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start_utc.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    interval = timedelta(minutes=args.interval_minutes)
    end = start + timedelta(days=args.days)
    expected = args.days * 24 * 60 // args.interval_minutes

    rows = []
    unreadable = []
    for directory in sorted(path for path in args.results.iterdir() if path.is_dir()):
        try:
            timestamp = parse_run_timestamp(directory.name)
        except ValueError:
            continue
        if not start <= timestamp < end:
            continue
        try:
            row = load_window(directory, start, interval)
        except Exception as exc:
            unreadable.append(f"{directory.name}: {exc}")
            continue
        if row:
            rows.append(row)

    if unreadable:
        print("unreadable windows:", *unreadable, sep="\n  ", file=sys.stderr)
    if len(rows) != expected:
        parser.error(f"expected {expected} readable windows, found {len(rows)}")

    scheduled = {row["scheduled_utc"] for row in rows}
    expected_schedule = {
        (start + index * interval).isoformat().replace("+00:00", "Z")
        for index in range(expected)
    }
    if scheduled != expected_schedule:
        missing = sorted(expected_schedule - scheduled)
        extra = sorted(scheduled - expected_schedule)
        parser.error(
            f"scheduled-block assignment differs from plan; "
            f"missing={missing}, extra={extra}"
        )
    if any(abs(row["directory_drift_s"]) > 5 for row in rows):
        parser.error("a directory timestamp drifted more than five seconds")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    window_csv = args.out_dir / "track_c_windows.csv"
    columns = list(rows[0])
    with window_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    completed_requests = sum(row["completed_ok"] or 0 for row in rows)
    request_errors = sum(row["request_errors"] or 0 for row in rows)
    dropped_admissions = sum(
        row["dropped_admissions"] or 0 for row in rows
    )
    request_attempts = completed_requests + request_errors
    admission_attempts = request_attempts + dropped_admissions
    report = {
        "overview": {
            "start_utc": args.start_utc,
            "end_utc": end.isoformat().replace("+00:00", "Z"),
            "expected_windows": expected,
            "included_windows": len(rows),
            "excluded_windows": 0,
            "completed_requests": completed_requests,
            "output_tokens": sum(row["output_tokens"] or 0 for row in rows),
            "request_errors": request_errors,
            "dropped_admissions": dropped_admissions,
            "request_attempts": request_attempts,
            "admission_attempts": admission_attempts,
            "request_error_rate": (
                request_errors / request_attempts if request_attempts else None
            ),
            "admission_drop_rate": (
                dropped_admissions / admission_attempts
                if admission_attempts
                else None
            ),
        },
        "endpoint_distributions": endpoint_distributions(
            rows, args.bootstrap_repetitions
        ),
        "daily": daily_summary(rows),
        "hourly": hourly_summary(rows),
        "slowest_windows": sorted(
            rows, key=lambda row: row["ttft_p99"], reverse=True
        )[:10],
        "telemetry_associations": telemetry_associations(rows),
        "quality": {
            "drifted_directory_timestamps": sum(
                abs(row["directory_drift_s"]) > 0.5 for row in rows
            ),
            "maximum_directory_drift_s": max(
                abs(row["directory_drift_s"]) for row in rows
            ),
            "drifted_controller_starts": sum(
                abs(row.get("controller_start_drift_s") or 0) > 0.5
                for row in rows
            ),
            "maximum_controller_start_drift_s": max(
                abs(row.get("controller_start_drift_s") or 0) for row in rows
            ),
            "drifted_telemetry_starts": sum(
                abs(row.get("telemetry_start_drift_s") or 0) > 0.5
                for row in rows
            ),
            "maximum_telemetry_start_drift_s": max(
                abs(row.get("telemetry_start_drift_s") or 0) for row in rows
            ),
            "valid_dcgm_windows": sum(row["dcgm_valid"] for row in rows),
            "short_metrics_windows": sum(
                (row.get("metrics_samples") or 0) < 120 for row in rows
            ),
            "windows_with_waiting": sum(
                (row.get("waiting_max") or 0) > 0 for row in rows
            ),
            "total_preemptions": sum(
                row.get("preemptions_delta") or 0 for row in rows
            ),
            "maximum_cpu_steal_sample": max(
                row.get("cpu_steal_max") or 0 for row in rows
            ),
            "windows_with_gpu_throttle_signal": sum(
                (row.get("gpu_power_capped_fraction") or 0) > 0
                or (row.get("gpu_thermal_slowdown_fraction") or 0) > 0
                for row in rows
            ),
        },
    }

    json_path = args.out_dir / "track_c_analysis.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    markdown_path = args.out_dir / "track_c_summary.md"
    write_markdown(report, markdown_path)
    print(f"windows analyzed: {len(rows)}")
    print(f"wrote {window_csv}")
    print(f"wrote {json_path}")
    print(f"wrote {markdown_path}")


if __name__ == "__main__":
    main()
