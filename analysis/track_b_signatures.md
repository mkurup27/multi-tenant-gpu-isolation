# Track B contention signature library

Block 1: 7 treatments, 2 controls, 2026-09-08T05:47Z to 2026-09-08T06:12Z. Baseline TTFT p99 0.0335s, TPOT p50 0.00650s, throughput 524.4 tok/s.
Block 2: 77 treatments, 19 controls, 2026-09-08T14:46Z to 2026-09-08T20:02Z. Baseline TTFT p99 0.0315s, TPOT p50 0.00650s, throughput 524.4 tok/s.

## Tenant-visible latency, as a ratio to the same-block control

Above 1.00 means the treatment made that quantity worse.

| layer | intensity | n | ttft_p50 | ttft_p99 | ttft_cv | tpot_p50 |
|---|---|---|---|---|---|---|
| cpu | 0.25 | 3 | 1.00 | 1.02 | 0.99 | 1.00 |
| cpu | 0.5 | 3 | 1.01 | 1.01 | 0.96 | 1.00 |
| cpu | 1.0 | 3 | 1.00 | 1.02 | 0.98 | 1.00 |
| hbm | 0.25 | 3 | 1.94 | 2.02 | 1.02 | 2.35 |
| hbm | 0.5 | 3 | 1.90 | 1.90 | 0.97 | 2.36 |
| hbm | 1.0 | 3 | 1.91 | 1.88 | 0.97 | 2.35 |
| network_delay | 0.25 | 3 | 1.37 | 1.42 | 0.94 | 1.00 |
| network_delay | 0.5 | 3 | 1.74 | 1.93 | 1.55 | 1.00 |
| network_delay | 1.0 | 3 | 2.52 | 3.12 | 1.97 | 1.00 |
| pcie | 0.25 | 3 | 1.05 | 1.16 | 1.06 | 1.03 |
| pcie | 0.5 | 3 | 1.02 | 0.99 | 0.83 | 1.03 |
| pcie | 1.0 | 3 | 1.04 | 1.02 | 0.97 | 1.03 |
| power | 0.25 | 3 | 1.01 | 1.03 | 1.01 | 1.00 |
| power | 0.5 | 3 | 1.00 | 1.09 | 1.08 | 1.01 |
| power | 1.0 | 3 | 1.64 | 1.68 | 1.09 | 1.95 |
| sm | 0.25 | 3 | 1.92 | 1.89 | 0.94 | 2.38 |
| sm | 0.5 | 3 | 1.92 | 1.96 | 0.99 | 2.38 |
| sm | 1.0 | 3 | 1.93 | 1.92 | 0.96 | 2.38 |
| storage | 0.25 | 3 | 1.03 | 1.16 | 1.12 | 1.00 |
| storage | 0.5 | 3 | 1.00 | 0.99 | 0.97 | 1.00 |
| storage | 1.0 | 3 | 0.99 | 1.02 | 0.95 | 1.00 |

## Telemetry, as absolute medians

Absolute rather than relative because several of these have a control baseline of exactly zero.

| layer | intensity | n | sm_clock_mhz | mem_clock_mhz | power_w | cpu_busy_median |
|---|---|---|---|---|---|---|
| control | 0.0 | 21 | 1980 | 2619 | 481 | 2 |
| cpu | 0.25 | 3 | 1980 | 2619 | 480 | 27 |
| cpu | 0.5 | 3 | 1980 | 2619 | 476 | 52 |
| cpu | 1.0 | 3 | 1980 | 2619 | 482 | 100 |
| hbm | 0.25 | 3 | 1980 | 2619 | 430 | 6 |
| hbm | 0.5 | 3 | 1980 | 2619 | 432 | 6 |
| hbm | 1.0 | 3 | 1980 | 2619 | 435 | 6 |
| network_delay | 0.25 | 3 | 1980 | 2619 | 482 | 2 |
| network_delay | 0.5 | 3 | 1980 | 2619 | 483 | 2 |
| network_delay | 1.0 | 3 | 1980 | 2619 | 475 | 2 |
| pcie | 0.25 | 3 | 1980 | 2619 | 478 | 7 |
| pcie | 0.5 | 3 | 1980 | 2619 | 476 | 7 |
| pcie | 1.0 | 3 | 1980 | 2619 | 481 | 7 |
| power | 0.25 | 3 | 1980 | 2619 | 482 | 2 |
| power | 0.5 | 3 | 1815 | 2619 | 449 | 2 |
| power | 1.0 | 3 | 345 | 2619 | 203 | 1 |
| sm | 0.25 | 3 | 1905 | 2619 | 668 | 6 |
| sm | 0.5 | 3 | 1905 | 2619 | 669 | 6 |
| sm | 1.0 | 3 | 1905 | 2619 | 670 | 6 |
| storage | 0.25 | 3 | 1980 | 2619 | 484 | 12 |
| storage | 0.5 | 3 | 1980 | 2619 | 479 | 22 |
| storage | 1.0 | 3 | 1980 | 2619 | 476 | 42 |

## Quantities that did not respond to any treatment

- tokens_per_s: 515.3 to 524.5, a spread under 5% across all treatments
- mem_clock_mhz: 2619 to 2619, a spread under 5% across all treatments
- gpu_util: 100 to 100, a spread under 5% across all treatments
