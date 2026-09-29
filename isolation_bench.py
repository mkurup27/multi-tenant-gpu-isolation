#!/usr/bin/env python3
"""Reproducible streaming benchmark for the GPU-isolation experiments.

Warm-up and measurement are separate admission intervals. Requests admitted
before the measurement interval never enter the measured sample. At the end of
the interval, admissions stop and already-admitted requests are allowed to
drain; both admission duration and completion span are reported.

Streaming cadence is reported as inter-chunk latency, not inter-token latency:
an SSE chunk may contain zero, one, or several tokens and providers chunk
differently.
"""

import argparse
import asyncio
import itertools
import json
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("Install the locked requirements: python3 -m pip install -r requirements.txt")


_VOCAB = (
    "system latency capacity tenant kernel memory bandwidth scheduler queue "
    "throughput partition hypervisor topology interconnect contention decode "
    "prefill batch cache pressure allocation boundary isolation neighbor host"
).split()

COUNTER_FRAGMENTS = ("preemptions_total",)
GAUGE_FRAGMENTS = (
    "num_requests_waiting",
    "num_requests_running",
    "kv_cache_usage_perc",
    "gpu_cache_usage_perc",
)


def build_prompts(count: int, word_counts, seed: int):
    rng = random.Random(seed)
    prompts = []
    for index in range(count):
        words = word_counts[index % len(word_counts)]
        body = " ".join(rng.choice(_VOCAB) for _ in range(words))
        prompts.append({
            "text": (
                f"Document {index}. Continue the text in the same register without "
                f"summarizing it.\n\n{body}\n\nContinuation:"
            ),
            "prompt_words_class": words,
        })
    return prompts


def percentile(values, percentage):
    if not values:
        return None
    ordered = sorted(values)
    position = int(round((percentage / 100) * (len(ordered) - 1)))
    return ordered[max(0, min(position, len(ordered) - 1))]


def distribution(values):
    if not values:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "mean": None, "cv": None}
    mean = statistics.mean(values)
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "mean": mean,
        "cv": statistics.pstdev(values) / mean if len(values) > 1 and mean else None,
    }


def parse_prometheus(text):
    values = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or " " not in line:
            continue
        metric, _, raw = line.rpartition(" ")
        if not any(fragment in metric for fragment in COUNTER_FRAGMENTS + GAUGE_FRAGMENTS):
            continue
        try:
            values[metric] = float(raw)
        except ValueError:
            continue
    return values


async def sample_metrics(url, interval, stop_event, samples):
    if not url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        while not stop_event.is_set():
            captured = {
                "wall_time": time.time(),
                "monotonic": time.perf_counter(),
                "values": {},
                "error": None,
            }
            try:
                response = await client.get(url)
                response.raise_for_status()
                captured["values"] = parse_prometheus(response.text)
            except Exception as exc:  # telemetry failure must not abort the workload
                captured["error"] = f"{type(exc).__name__}: {exc}"
            samples.append(captured)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


async def one_request(client, args, prompt, index, phase, interval_start):
    admitted = time.perf_counter()
    record = {
        "idx": index,
        "phase": phase,
        "admitted_wall_time": time.time(),
        "admitted_offset_s": admitted - interval_start,
        "completed_offset_s": None,
        "ttft_s": None,
        "e2e_s": None,
        "tpot_s": None,
        "inter_chunk_delays_s": [],
        "stream_content_chunks": 0,
        "output_tokens": None,
        "prompt_tokens": None,
        "finish_reason": None,
        "prompt_words_class": prompt["prompt_words_class"],
        "admission_dropped": False,
        "error": None,
    }
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt["text"]}],
        "max_tokens": args.max_tokens,
        "temperature": 0.0,
        "seed": args.seed,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.ignore_eos:
        body["ignore_eos"] = True

    last_content = None
    try:
        async with client.stream("POST", "/chat/completions", json=body) as response:
            if response.status_code != 200:
                text = (await response.aread()).decode(errors="replace")[:500]
                record["error"] = f"HTTP {response.status_code}: {text}"
            else:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if usage:
                        record["prompt_tokens"] = usage.get("prompt_tokens")
                        record["output_tokens"] = usage.get("completion_tokens")
                    for choice in chunk.get("choices") or []:
                        if choice.get("finish_reason"):
                            record["finish_reason"] = choice["finish_reason"]
                        content = (choice.get("delta") or {}).get("content")
                        if not content:
                            continue
                        now = time.perf_counter()
                        record["stream_content_chunks"] += 1
                        if record["ttft_s"] is None:
                            record["ttft_s"] = now - admitted
                        elif last_content is not None:
                            record["inter_chunk_delays_s"].append(now - last_content)
                        last_content = now
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"

    completed = time.perf_counter()
    record["e2e_s"] = completed - admitted
    record["completed_offset_s"] = completed - interval_start
    if (
        record["ttft_s"] is not None
        and record["output_tokens"] is not None
        and record["output_tokens"] > 1
    ):
        record["tpot_s"] = (
            record["e2e_s"] - record["ttft_s"]
        ) / (record["output_tokens"] - 1)
    return record


async def closed_loop_interval(client, args, prompts, phase, duration, counter):
    interval_start = time.perf_counter()
    admission_deadline = interval_start + duration
    records = []

    async def worker():
        while time.perf_counter() < admission_deadline:
            index = next(counter)
            record = await one_request(
                client,
                args,
                prompts[index % len(prompts)],
                index,
                phase,
                interval_start,
            )
            records.append(record)

    await asyncio.gather(*(worker() for _ in range(args.concurrency)))
    completed = time.perf_counter()
    return records, {
        "admission_duration_s": duration,
        "completion_span_s": completed - interval_start,
        "dropped_admissions": 0,
    }


async def poisson_interval(client, args, prompts, phase, duration, counter, rng):
    interval_start = time.perf_counter()
    admission_deadline = interval_start + duration
    records = []
    active = set()
    dropped = 0
    next_arrival = interval_start

    async def fire(index):
        return await one_request(
            client,
            args,
            prompts[index % len(prompts)],
            index,
            phase,
            interval_start,
        )

    while next_arrival < admission_deadline:
        await asyncio.sleep(max(0, next_arrival - time.perf_counter()))
        finished = {task for task in active if task.done()}
        for task in finished:
            active.remove(task)
            records.append(task.result())

        index = next(counter)
        if len(active) >= args.max_outstanding:
            dropped += 1
            now = time.perf_counter()
            records.append(
                {
                    "idx": index,
                    "phase": phase,
                    "admitted_wall_time": time.time(),
                    "admitted_offset_s": now - interval_start,
                    "completed_offset_s": now - interval_start,
                    "ttft_s": None,
                    "e2e_s": None,
                    "tpot_s": None,
                    "inter_chunk_delays_s": [],
                    "stream_content_chunks": 0,
                    "output_tokens": None,
                    "prompt_tokens": None,
                    "finish_reason": None,
                    "prompt_words_class": prompts[index % len(prompts)][
                        "prompt_words_class"
                    ],
                    "admission_dropped": True,
                    "error": "client_admission_limit",
                }
            )
        else:
            active.add(asyncio.create_task(fire(index)))
        next_arrival += rng.expovariate(args.rate)

    if active:
        records.extend(await asyncio.gather(*active))
    completed = time.perf_counter()
    return records, {
        "admission_duration_s": duration,
        "completion_span_s": completed - interval_start,
        "dropped_admissions": dropped,
    }


async def run_interval(client, args, prompts, phase, duration, counter, rng):
    if duration <= 0:
        return [], {"admission_duration_s": 0, "completion_span_s": 0, "dropped_admissions": 0}
    if args.arrival == "closed":
        return await closed_loop_interval(client, args, prompts, phase, duration, counter)
    return await poisson_interval(client, args, prompts, phase, duration, counter, rng)


def environment_stamp():
    def command(argv):
        try:
            return subprocess.run(
                argv, capture_output=True, text=True, timeout=15, check=False
            ).stdout.strip()
        except Exception:
            return None

    return {
        "hostname": command(["hostname", "-f"]),
        "kernel": command(["uname", "-r"]),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def metric_summary(samples):
    successful = [sample for sample in samples if not sample["error"]]
    all_names = sorted(
        {name for sample in successful for name in sample["values"]}
    )
    result = {}
    for name in all_names:
        values = [sample["values"][name] for sample in successful if name in sample["values"]]
        item = distribution(values)
        item["first"] = values[0] if values else None
        item["last"] = values[-1] if values else None
        if any(fragment in name for fragment in COUNTER_FRAGMENTS):
            item["delta"] = values[-1] - values[0] if len(values) > 1 else None
        result[name] = item
    return {
        "samples": len(samples),
        "successful_samples": len(successful),
        "metrics": result,
    }


def summarize(args, measured, timing, metric_samples):
    completed = [
        record
        for record in measured
        if not record["error"] and not record["admission_dropped"]
    ]
    errors = [
        record
        for record in measured
        if record["error"] and not record["admission_dropped"]
    ]
    dropped = [record for record in measured if record["admission_dropped"]]
    ttft = [record["ttft_s"] for record in completed if record["ttft_s"] is not None]
    e2e = [record["e2e_s"] for record in completed if record["e2e_s"] is not None]
    tpot = [record["tpot_s"] for record in completed if record["tpot_s"] is not None]
    inter_chunk = [
        delay for record in completed for delay in record["inter_chunk_delays_s"]
    ]
    output_tokens = sum(record["output_tokens"] or 0 for record in completed)
    completion_span = timing["completion_span_s"]

    return {
        "schema_version": 2,
        "label": args.label,
        "tier": args.tier,
        "comparison_group": args.comparison_group,
        "config": {
            "model": args.model,
            "arrival": args.arrival,
            "concurrency": args.concurrency,
            "rate_rps": args.rate if args.arrival == "poisson" else None,
            "max_outstanding": args.max_outstanding,
            "measurement_admission_duration_s": args.duration,
            "warmup_admission_duration_s": args.warmup,
            "max_tokens": args.max_tokens,
            "prompt_words": args.prompt_words,
            "prompt_words_set": args.prompt_words_set,
            "ignore_eos": args.ignore_eos,
            "seed": args.seed,
        },
        "environment": environment_stamp(),
        "timing": timing,
        "counts": {
            "completed_ok": len(completed),
            "request_errors": len(errors),
            "dropped_admissions": len(dropped),
            "output_tokens": output_tokens,
        },
        "throughput": {
            "tokens_per_completion_span_s": (
                output_tokens / completion_span if completion_span else None
            ),
            "requests_per_completion_span_s": (
                len(completed) / completion_span if completion_span else None
            ),
        },
        "ttft_s": distribution(ttft),
        "e2e_s": distribution(e2e),
        "tpot_s": distribution(tpot),
        "inter_chunk_latency_s": distribution(inter_chunk),
        "server_metrics": metric_summary(metric_samples),
        "errors_sample": [record["error"] for record in errors[:5]],
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", "EMPTY"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--arrival", choices=("closed", "poisson"), default="poisson")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--rate", type=float, default=2.0)
    parser.add_argument("--max-outstanding", type=int, default=64)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--warmup", type=float, default=30)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--prompt-words", type=int, default=380)
    parser.add_argument(
        "--prompt-words-set",
        help="comma-separated prompt-length classes, e.g. 100,1600",
    )
    parser.add_argument("--num-prompts", type=int, default=32)
    parser.add_argument("--seed", type=int, default=20260903)
    parser.add_argument(
        "--ignore-eos",
        action="store_true",
        help="vLLM-only extension; do not use for managed endpoints",
    )
    parser.add_argument("--metrics-url")
    parser.add_argument("--metrics-interval", type=float, default=1.0)
    parser.add_argument("--label", default="unlabeled")
    parser.add_argument("--tier", default="unknown")
    parser.add_argument(
        "--comparison-group",
        default="within-tier-only",
        help="Only runs with the same non-default group are directly comparable",
    )
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.duration <= 0 or args.warmup < 0:
        parser.error("duration must be > 0 and warmup must be >= 0")
    if args.arrival == "poisson" and args.rate <= 0:
        parser.error("rate must be > 0 for poisson arrivals")
    if args.max_outstanding < 1:
        parser.error("max-outstanding must be >= 1")

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.prompt_words_set:
        try:
            word_counts = [int(value) for value in args.prompt_words_set.split(",")]
        except ValueError:
            parser.error("--prompt-words-set must contain comma-separated integers")
        if not word_counts or any(value <= 0 for value in word_counts):
            parser.error("--prompt-words-set values must be positive")
    else:
        word_counts = [args.prompt_words]
    prompts = build_prompts(args.num_prompts, word_counts, args.seed)
    counter = itertools.count()
    rng = random.Random(args.seed)

    limits = httpx.Limits(
        max_connections=max(args.max_outstanding, args.concurrency, 64),
        max_keepalive_connections=0,
    )
    headers = {"Authorization": f"Bearer {args.api_key}"}
    timeout = httpx.Timeout(600)

    async with httpx.AsyncClient(
        base_url=args.base_url,
        headers=headers,
        timeout=timeout,
        limits=limits,
    ) as client:
        warmup_records, _ = await run_interval(
            client, args, prompts, "warmup", args.warmup, counter, rng
        )

        metric_samples = []
        stop_metrics = asyncio.Event()
        sampler = asyncio.create_task(
            sample_metrics(
                args.metrics_url,
                args.metrics_interval,
                stop_metrics,
                metric_samples,
            )
        )
        measured_records, timing = await run_interval(
            client, args, prompts, "measure", args.duration, counter, rng
        )
        stop_metrics.set()
        await sampler

    records = warmup_records + measured_records
    summary = summarize(args, measured_records, timing, metric_samples)

    with Path(f"{args.out}.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    with Path(f"{args.out}.metrics.jsonl").open("w") as handle:
        for sample in metric_samples:
            handle.write(json.dumps(sample) + "\n")
    with Path(f"{args.out}.summary.json").open("w") as handle:
        json.dump(summary, handle, indent=2)

    ttft = summary["ttft_s"]
    throughput = summary["throughput"]["tokens_per_completion_span_s"]
    if ttft["p50"] is None:
        print(f"[{args.label}] NO USABLE MEASURED REQUESTS")
        return
    cv_text = f"{ttft['cv']:.3f}" if ttft["cv"] is not None else "n/a"
    print(
        f"[{args.label}] tier={args.tier} ok={summary['counts']['completed_ok']} "
        f"errors={summary['counts']['request_errors']} "
        f"dropped={summary['counts']['dropped_admissions']} "
        f"tok/s={throughput:.2f} ttft_p50={ttft['p50']:.3f} "
        f"ttft_p99={ttft['p99']:.3f} ttft_cv={cv_text}"
    )


if __name__ == "__main__":
    asyncio.run(main())
