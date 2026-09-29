# Track C: seven-day longitudinal result

Included windows: 336 of 336. Excluded: 0. Request errors: 0. Admission drops: 0.
Request-error rate: 0.0% (0/161616). Admission-drop rate: 0.0% (0/161616).

Intervals below are 95% day-block bootstrap intervals over seven 24-hour study days. With seven blocks, tail intervals are imprecise.

## Window-level endpoint distributions

| endpoint | median | p90 | p95 | p99 |
| --- | --- | --- | --- | --- |
| TTFT p99 (s) | 0.05149 [0.04817, 0.05446] | 0.06954 [0.0647, 0.07164] | 0.07363 [0.07136, 0.07551] | 0.0828 [0.07667, 0.08556] |
| TTFT p50 (s) | 0.02886 [0.02858, 0.02914] | 0.03112 [0.03027, 0.03155] | 0.03164 [0.03101, 0.0325] | 0.03288 [0.03187, 0.0331] |
| TTFT coefficient of variation | 0.1873 [0.1749, 0.2013] | 0.2647 [0.2454, 0.2736] | 0.2896 [0.2728, 0.302] | 0.3533 [0.308, 0.3822] |
| end-to-end p99 (s) | 1.928 [1.926, 1.93] | 1.939 [1.937, 1.942] | 1.944 [1.942, 1.945] | 1.95 [1.948, 1.955] |
| TPOT p50 (s) | 0.007139 [0.007138, 0.00714] | 0.007145 [0.007143, 0.007146] | 0.007146 [0.007144, 0.007148] | 0.007148 [0.007146, 0.007149] |
| output-token throughput (tokens/s) | 1012.07 [1012.06, 1012.07] | 1012.12 [1012.11, 1012.12] | 1012.13 [1012.12, 1012.13] | 1012.14 [1012.13, 1012.14] |

## Daily primary endpoint

| study day | start UTC | windows | TTFT p99 median | TTFT p99 p95 |
| --- | --- | --- | --- | --- |
| 1 | 2026-09-07T05:00:00Z | 48 | 0.04867 | 0.06125 |
| 2 | 2026-09-08T05:00:00Z | 48 | 0.05543 | 0.0763 |
| 3 | 2026-09-09T05:00:00Z | 48 | 0.04656 | 0.07382 |
| 4 | 2026-09-10T05:00:00Z | 48 | 0.04783 | 0.06852 |
| 5 | 2026-09-11T05:00:00Z | 48 | 0.05375 | 0.07199 |
| 6 | 2026-09-12T05:00:00Z | 48 | 0.05207 | 0.076 |
| 7 | 2026-09-13T05:00:00Z | 48 | 0.05755 | 0.07398 |

## Descriptive UTC-hour effects

| UTC hour | windows | median daily-hour TTFT p99 | overall ratio | daily range |
| --- | --- | --- | --- | --- |
| 00:00 | 14 | 0.05147 | 0.9996 | 0.03845-0.06416 |
| 01:00 | 14 | 0.0484 | 0.94 | 0.0407-0.06334 |
| 02:00 | 14 | 0.04591 | 0.8916 | 0.03731-0.05622 |
| 03:00 | 14 | 0.04686 | 0.9101 | 0.04083-0.07621 |
| 04:00 | 14 | 0.04487 | 0.8715 | 0.04186-0.0564 |
| 05:00 | 14 | 0.04498 | 0.8737 | 0.04005-0.06067 |
| 06:00 | 14 | 0.05347 | 1.038 | 0.03975-0.06408 |
| 07:00 | 14 | 0.05109 | 0.9922 | 0.0459-0.07666 |
| 08:00 | 14 | 0.05343 | 1.038 | 0.04406-0.0675 |
| 09:00 | 14 | 0.05299 | 1.029 | 0.04636-0.06405 |
| 10:00 | 14 | 0.05119 | 0.9942 | 0.04546-0.06537 |
| 11:00 | 14 | 0.04641 | 0.9013 | 0.03916-0.05271 |
| 12:00 | 14 | 0.05283 | 1.026 | 0.04342-0.07123 |
| 13:00 | 14 | 0.05259 | 1.021 | 0.04228-0.06372 |
| 14:00 | 14 | 0.05528 | 1.074 | 0.04591-0.0649 |
| 15:00 | 14 | 0.05177 | 1.006 | 0.04021-0.06019 |
| 16:00 | 14 | 0.05856 | 1.137 | 0.04453-0.06935 |
| 17:00 | 14 | 0.05888 | 1.143 | 0.04439-0.069 |
| 18:00 | 14 | 0.0659 | 1.28 | 0.04787-0.07067 |
| 19:00 | 14 | 0.05861 | 1.138 | 0.04363-0.0687 |
| 20:00 | 14 | 0.05998 | 1.165 | 0.04365-0.07422 |
| 21:00 | 14 | 0.05221 | 1.014 | 0.04293-0.06666 |
| 22:00 | 14 | 0.05857 | 1.138 | 0.04468-0.06946 |
| 23:00 | 14 | 0.04875 | 0.9469 | 0.04594-0.05961 |

Hour effects are unadjusted and descriptive; no set of 24 significance tests was run. Reporting every hour avoids selecting only elevated periods.

## Slowest windows

| scheduled UTC | TTFT p99 | TTFT p50 | TPOT p50 | tok/s | SM clock | power | CPU steal p95 | waiting max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-10T16:30:00Z | 0.08933 | 0.03163 | 0.007141 | 1012 | 1980 | 449.2 | 0 | 0 |
| 2026-09-08T20:00:00Z | 0.08683 | 0.0326 | 0.007146 | 1012 | 1980 | 458.2 | 0 | 0 |
| 2026-09-09T20:30:00Z | 0.08322 | 0.03027 | 0.00714 | 1012 | 1980 | 450.1 | 0 | 0 |
| 2026-09-12T08:00:00Z | 0.0832 | 0.03185 | 0.007135 | 1012 | 1980 | 449.7 | 0 | 0 |
| 2026-09-08T19:00:00Z | 0.08205 | 0.03303 | 0.007148 | 1012 | 1980 | 458.3 | 0 | 0 |
| 2026-09-14T03:00:00Z | 0.08138 | 0.03292 | 0.007142 | 1012 | 1980 | 451.2 | 0 | 0 |
| 2026-09-12T07:00:00Z | 0.07966 | 0.03365 | 0.007145 | 1012 | 1980 | 449.4 | 0 | 0 |
| 2026-09-12T06:30:00Z | 0.07726 | 0.02932 | 0.007144 | 1012 | 1980 | 449.9 | 0 | 0 |
| 2026-09-08T18:00:00Z | 0.07702 | 0.03314 | 0.007142 | 1012 | 1980 | 450.1 | 0 | 0 |
| 2026-09-11T22:00:00Z | 0.0766 | 0.03078 | 0.00714 | 1012 | 1980 | 458.1 | 0 | 0 |

## Measured-interval telemetry alongside the slowest decile

| signal | slowest decile median | other windows median | ratio | Spearman rho |
| --- | --- | --- | --- | --- |
| vLLM running requests maximum | 17 | 16 | 1.062 | 0.7246 |
| vLLM KV-cache usage maximum | 0.02612 | 0.02273 | 1.149 | 0.6513 |
| VPC receive rate (Mbit/s) | 0.6641 | 0.6691 | 0.9925 | -0.4081 |
| VPC transmit rate (Mbit/s) | 2.839 | 2.838 | 1 | 0.1421 |
| root-disk utilization p95 (%) | 0.8 | 0.8 | 1 | -0.06261 |
| GPU temperature p95 (C) | 43 | 43 | 1 | 0.05243 |
| CPU steal p95 (%) | 0 | 0 | - | -0.05041 |
| GPU power median (W) | 450.1 | 449.7 | 1.001 | 0.05015 |
| CPU iowait p95 (%) | 0 | 0 | - | -0.04686 |
| GPU SM clock median (MHz) | 1980 | 1980 | 1 | - |
| GPU utilization median (%) | 100 | 100 | 1 | - |
| GPU memory utilization median (%) | 85 | 85 | 1 | - |
| vLLM waiting requests maximum | 0 | 0 | - | - |

Host telemetry is restricted by sample offset to the 120-second measured admission interval after the 30-second warm-up. Associations are unadjusted descriptive comparisons across serially correlated windows. Running-request and KV-cache maxima describe realized workload and may be consequences of slower requests.

## Data quality

- Directory timestamps more than 0.5 seconds from their assigned block: 3. Maximum drift: 1 seconds.
- Controller starts more than 0.5 seconds late: 6. Maximum drift: 1 seconds.
- Host telemetry starts more than 0.5 seconds late: 224. Maximum drift: 1 seconds.
- Windows with valid DCGM samples: 0 of 336. DCGM was optional and is not used above.
- Windows with fewer than 120 successful vLLM metric samples: 0.
- Windows with any waiting request: 0. Total preemptions: 0.
- Maximum one-second aggregate CPU-steal sample: 0.1%. Window-level p95 was zero in both the slowest decile and other windows.
- Power-cap or thermal-slowdown samples: 0 windows with any active flag.
