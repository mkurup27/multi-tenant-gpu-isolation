#!/usr/bin/env python3
"""
antagonists.py — deliberate contention generators, one per GPU-side layer.

Track B needs each isolation layer loaded in isolation so the victim
workload's response has exactly one candidate cause. These run as a
separate process against the same GPU as the victim.

Intensity is a duty cycle (0.0-1.0) rather than an on/off switch, so each
signature comes with a dose-response curve instead of a single bit.

  python3 antagonists.py sm    --intensity 0.5 --duration 180
  python3 antagonists.py hbm   --intensity 1.0 --duration 180
  python3 antagonists.py pcie  --intensity 1.0 --duration 180

Host CPU, network and storage antagonists are not here — they are
stress-ng, tc/iperf3 and fio respectively, driven from the runbook.
"""

import argparse
import signal
import sys
import time

import torch


def _cycle(fn, intensity, duration, device):
    """Run fn in 100ms periods at the given duty cycle."""
    period = 0.1
    on = period * max(0.0, min(1.0, intensity))
    end = time.monotonic() + duration
    while time.monotonic() < end:
        t0 = time.monotonic()
        while time.monotonic() - t0 < on:
            with torch.inference_mode():
                fn()
        torch.cuda.synchronize(device)
        rest = period - (time.monotonic() - t0)
        if rest > 0:
            time.sleep(rest)


def sm_antagonist(args):
    """Saturate SMs with dense GEMM. High SM activity, modest DRAM traffic."""
    dev = torch.device(f"cuda:{args.gpu}")
    n = args.size
    a = torch.randn(n, n, device=dev, dtype=torch.float16)
    b = torch.randn(n, n, device=dev, dtype=torch.float16)
    print(f"[sm] {n}x{n} fp16 GEMM, intensity={args.intensity}, {args.duration}s")
    _cycle(lambda: torch.mm(a, b), args.intensity, args.duration, dev)


def hbm_antagonist(args):
    """Saturate HBM bandwidth with large strided copies.

    This is the layer the article expects to matter most: decode is
    memory-bound, so this should inflate streaming cadence and throughput.
    """
    dev = torch.device(f"cuda:{args.gpu}")
    free, _ = torch.cuda.mem_get_info(dev)
    # Use a slice of free VRAM large enough to defeat L2, leaving room for
    # the victim. Three buffers of this size are allocated.
    elems = int(min(free * args.memory_fraction, args.max_buffer_gib * (1 << 30))) // 4
    x = torch.empty(elems, device=dev, dtype=torch.float32).fill_(1.0)
    y = torch.empty(elems, device=dev, dtype=torch.float32).fill_(2.0)
    z = torch.empty(elems, device=dev, dtype=torch.float32)
    gib = elems * 4 / (1 << 30)
    print(f"[hbm] triad over {gib:.2f} GiB buffers, intensity={args.intensity}")
    _cycle(lambda: torch.add(x, y, alpha=2.0, out=z), args.intensity, args.duration, dev)


def pcie_antagonist(args):
    """Saturate the host-to-device path with pinned-memory transfers.

    Expected to produce a NULL on steady single-GPU decode and a real
    effect on model load. Publishing that null is the point.
    """
    dev = torch.device(f"cuda:{args.gpu}")
    mb = args.chunk_mb
    host = torch.empty(mb * 1024 * 1024 // 4, dtype=torch.float32).pin_memory()
    gpu = torch.empty_like(host, device=dev)
    print(f"[pcie] {mb} MiB H2D+D2H ping-pong, intensity={args.intensity}")

    def step():
        gpu.copy_(host, non_blocking=True)
        host.copy_(gpu, non_blocking=True)

    _cycle(step, args.intensity, args.duration, dev)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("layer", choices=["sm", "hbm", "pcie"])
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--intensity", type=float, default=1.0,
                    help="duty cycle 0.0-1.0; use 0.0/0.25/0.5/1.0 for the ladder")
    ap.add_argument("--duration", type=float, default=180)
    ap.add_argument("--size", type=int, default=8192, help="GEMM dimension (sm)")
    ap.add_argument("--chunk-mb", type=int, default=256, help="transfer size (pcie)")
    ap.add_argument(
        "--memory-fraction",
        type=float,
        default=0.08,
        help="fraction of currently free VRAM per HBM buffer; three buffers are used",
    )
    ap.add_argument("--max-buffer-gib", type=float, default=4.0)
    args = ap.parse_args()

    if not 0 <= args.intensity <= 1:
        ap.error("--intensity must be between 0 and 1")
    if not 0 < args.memory_fraction <= 0.2:
        ap.error("--memory-fraction must be greater than 0 and at most 0.2")

    if args.intensity <= 0:
        print(f"[{args.layer}] intensity 0 — control arm, idling {args.duration}s")
        time.sleep(args.duration)
        return

    def interrupted(_signum, _frame):
        print(f"[{args.layer}] interrupted; CUDA allocations will be released", flush=True)
        sys.exit(130)

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    torch.cuda.set_device(args.gpu)
    {"sm": sm_antagonist, "hbm": hbm_antagonist, "pcie": pcie_antagonist}[args.layer](args)
    print(f"[{args.layer}] done")


if __name__ == "__main__":
    main()
