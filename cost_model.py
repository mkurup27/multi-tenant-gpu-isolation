#!/usr/bin/env python3
"""Deterministic cost arithmetic for the isolation decision framework."""

import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--hourly-price", required=True, type=float)
    parser.add_argument("--hours", required=True, type=float)
    parser.add_argument("--output-tokens", required=True, type=int)
    parser.add_argument("--usable-requests", required=True, type=int)
    parser.add_argument(
        "--headroom-factor",
        type=float,
        default=1.0,
        help="capacity multiplier required to hold the preregistered p99 SLA",
    )
    args = parser.parse_args()
    if args.output_tokens <= 0 or args.usable_requests <= 0:
        parser.error("output-tokens and usable-requests must be positive")
    if args.headroom_factor < 1:
        parser.error("headroom-factor must be at least 1")

    base_cost = args.hourly_price * args.hours
    sla_cost = base_cost * args.headroom_factor
    result = {
        "name": args.name,
        "inputs": {
            "hourly_price": args.hourly_price,
            "hours": args.hours,
            "output_tokens": args.output_tokens,
            "usable_requests": args.usable_requests,
            "headroom_factor": args.headroom_factor,
        },
        "base_cost": base_cost,
        "sla_adjusted_cost": sla_cost,
        "base_cost_per_million_output_tokens": base_cost / args.output_tokens * 1_000_000,
        "sla_adjusted_cost_per_million_output_tokens": (
            sla_cost / args.output_tokens * 1_000_000
        ),
        "base_cost_per_usable_request": base_cost / args.usable_requests,
        "sla_adjusted_cost_per_usable_request": sla_cost / args.usable_requests,
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
