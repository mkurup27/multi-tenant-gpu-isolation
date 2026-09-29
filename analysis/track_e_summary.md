# Track E: self-inflicted contention

Cases analysed: 13 of 13 directories. Request errors across all cases: 0. Client admission drops: 0.

## Concurrency ramp, closed loop

Ratios are against concurrency 1, the lowest level run.

| concurrency | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | tok/s | waiting | KV usage | preemptions |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 111 | 0 | 0.01633 | 0.02353 | 0.006347 | 156.5 | 0.0 | 0.002 | 0 |
| 8 | 840 | 0 | 0.02572 | 0.04569 | 0.006658 | 1184.3 | 0.0 | 0.014 | 0 |
| 32 | 2902 | 0 | 0.09172 | 0.1783 | 0.007462 | 4088.4 | 0.0 | 0.053 | 0 |
| 64 | 4913 | 0 | 0.1725 | 0.3397 | 0.0086 | 6906.1 | 0.0 | 0.073 | 0 |
| 128 | 7285 | 0 | 0.399 | 0.6579 | 0.01093 | 10226.8 | 0.0 | 0.111 | 0 |
| 256 | 8448 | 0 | 2.734 | 3.493 | 0.01108 | 11711.4 | 128.0 | 0.120 | 0 |
| 512 | 8631 | 0 | 8.268 | 9.327 | 0.0112 | 11613.0 | 384.0 | 0.119 | 0 |

Relative to the lowest level:

- TTFT p50: c1=1.00x, c8=1.57x, c32=5.62x, c64=10.56x, c128=24.43x, c256=167.40x, c512=506.17x
- TTFT p99: c1=1.00x, c8=1.94x, c32=7.58x, c64=14.44x, c128=27.96x, c256=148.47x, c512=396.47x
- TPOT p50: c1=1.00x, c8=1.05x, c32=1.18x, c64=1.35x, c128=1.72x, c256=1.75x, c512=1.76x
- throughput: c1=1.00x, c8=7.57x, c32=26.12x, c64=44.13x, c128=65.35x, c256=74.84x, c512=74.21x

## Mixed prompt lengths, split by prompt class

Every request below shared a window with the other class. Splitting by class asks what a long prompt does to a short one, which is the question an averaged window cannot answer.

| case | prompt words | n | TTFT p50 | TTFT p99 | TPOT p50 |
|---|---|---|---|---|---|
| mixed_lengths_r1 | 100 | 179 | 0.02442 | 0.03289 | 0.006621 |
| mixed_lengths_r1 | 1600 | 179 | 0.02767 | 0.03773 | 0.00663 |
| mixed_lengths_r2 | 100 | 179 | 0.02474 | 0.03296 | 0.00662 |
| mixed_lengths_r2 | 1600 | 179 | 0.02801 | 0.04123 | 0.00663 |
| mixed_lengths_r3 | 100 | 179 | 0.02336 | 0.03119 | 0.00662 |
| mixed_lengths_r3 | 1600 | 179 | 0.02742 | 0.03393 | 0.006632 |

Pooled across repeats:

- 100 words, n=537: TTFT p50 0.02432s, p99 0.03289s, TPOT p50 0.00662s
- 1600 words, n=537: TTFT p50 0.02765s, p99 0.03906s, TPOT p50 0.00663s
- TTFT p50, 1600-word over 100-word: 1.14x
- TPOT p50, 1600-word over 100-word: 1.00x

## KV-cache pressure

Whether pressure materialised is decided by the KV gauge and the preemption delta, not by the intent of the configuration.

| case | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | KV p50 | KV max | waiting | preemptions |
|---|---|---|---|---|---|---|---|---|---|
| kv_pressure_r1 | 1219 | 0 | 0.3755 | 0.6135 | 0.0161 | 0.394 | 0.678 | 0.0 | 0 |
| kv_pressure_r2 | 1223 | 0 | 0.3686 | 0.7168 | 0.01609 | 0.392 | 0.673 | 0.0 | 0 |
| kv_pressure_r3 | 1221 | 0 | 0.3818 | 0.5943 | 0.01609 | 0.399 | 0.675 | 0.0 | 0 |

Peak KV-cache usage reached 67.8% of capacity. The cache never approached exhaustion, so this arm did not produce the intended pressure and must be reported as such.
Preemptions across the arm: 0. No request was ever preempted.
