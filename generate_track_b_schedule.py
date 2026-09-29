#!/usr/bin/env python3
"""Create a blocked, randomized, reproducible Track B treatment schedule."""

import argparse
import csv
import random


LAYERS = ("sm", "hbm", "pcie", "cpu", "storage", "network_delay", "power")
INTENSITIES = (0.25, 0.5, 1.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--control-every", type=int, default=4)
    parser.add_argument("--output", default="-")
    args = parser.parse_args()
    rng = random.Random(args.seed)
    rows = []
    sequence = 0

    for repeat in range(1, args.repeats + 1):
        treatments = [(layer, intensity) for layer in LAYERS for intensity in INTENSITIES]
        rng.shuffle(treatments)
        for offset, (layer, intensity) in enumerate(treatments):
            if offset % args.control_every == 0:
                sequence += 1
                rows.append((sequence, repeat, "control", 0.0))
            sequence += 1
            rows.append((sequence, repeat, layer, intensity))
        sequence += 1
        rows.append((sequence, repeat, "control", 0.0))

    target = open(args.output, "w", newline="") if args.output != "-" else None
    handle = target or __import__("sys").stdout
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(("sequence", "repeat", "layer", "intensity"))
    writer.writerows(rows)
    if target:
        target.close()


if __name__ == "__main__":
    main()
