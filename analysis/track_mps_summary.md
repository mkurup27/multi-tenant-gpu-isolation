# Track G: MPS scheduling

Cases analysed: 9 of 9 directories. Request errors: 0. Client admission drops: 0.

MPS cases with two-client validation: 3 of 3.

## Per-configuration medians

| configuration | repeats | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | TPOT range | tok/s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| undivided GPU, victim alone (`m1_undivided_solo`) | 3 | 747 | 0 | 0.02560 | 0.03169 | 0.00670 | 0.00669-0.00670 | 524.2 |
| normal time-sharing with HBM antagonist (`m2_time_sharing`) | 3 | 747 | 0 | 0.04807 | 0.05872 | 0.01525 | 0.01523-0.01525 | 515.5 |
| MPS at 50/50 active-thread limits with HBM antagonist (`m3_mps`) | 3 | 747 | 0 | 0.06623 | 0.08023 | 0.02272 | 0.02271-0.02277 | 507.8 |

## Treatment effects

- Time-sharing TPOT over victim-alone baseline: 2.28x
- Time-sharing TTFT p99 over victim-alone baseline: 1.85x
- MPS TPOT over victim-alone baseline: 3.39x
- MPS TTFT p99 over victim-alone baseline: 2.53x
- MPS throughput over victim-alone baseline: 0.97x
- MPS TPOT over normal time-sharing: 1.49x
- MPS TTFT p99 over normal time-sharing: 1.37x

The MPS arm applies a 50% active-thread limit to both clients. That limits executable threads; it does not create MIG-style dedicated memory bandwidth. Interpret the result as this exact MPS policy under an HBM antagonist, not as a universal MPS effect.
