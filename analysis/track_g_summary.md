# Track G: MIG partitioning

Cases analysed: 12 of 12 directories. Request errors: 0. Client admission drops: 0.

## Per-configuration medians

| configuration | repeats | ok | errors | TTFT p50 | TTFT p99 | TPOT p50 | TPOT range | tok/s |
|---|---|---|---|---|---|---|---|---|
| MIG slice, neighbour idle (`g1_mig_solo`) | 3 | 747 | 0 | 0.03988 | 0.05456 | 0.01188 | 0.01188-0.01188 | 518.9 |
| MIG slice, HBM antagonist next door (`g2_mig_antagonist`) | 3 | 747 | 0 | 0.03836 | 0.04809 | 0.012 | 0.012-0.01201 | 518.8 |
| two MIG slices, 1 rps each (`g3_mig_dual`) | 3 | 792 | 0 | 0.03673 | 0.05239 | 0.01178 | 0.01178-0.01178 | 554.0 |
| undivided GPU, 2 rps (`g4_undivided`) | 3 | 747 | 0 | 0.02561 | 0.03167 | 0.006693 | 0.006692-0.006696 | 524.2 |

## Does a slice contain a hostile neighbour?

- TPOT p50, antagonist over idle neighbour: 0.012s vs 0.01188s = 1.01x
- TTFT p50, antagonist over idle neighbour: 0.03836s vs 0.03988s = 0.96x
- The antagonist moved TPOT by 1.0% across the arm. No material TPOT degradation appeared under this treatment.

## What does a slice cost when nobody is hostile?

- TPOT p50, one slice over undivided card at equal rate: 0.01188s vs 0.006693s = 1.78x
- TTFT p50, one slice over undivided card at equal rate: 0.03988s vs 0.02561s = 1.56x
- Cross-check, g4 against the Track B undivided control: 0.006693s vs 0.006496s = 1.03x

## The pair against the undivided card, at equal offered load

- Aggregate throughput, two slices over undivided: 554 tok/s vs 524.2 tok/s = 1.06x
- Per-request TPOT p50, slice over undivided: 0.01178s vs 0.006693s = 1.76x
- KV capacity: 286,912 tokens across two slices vs 408,256 on the undivided card, a 29.7% loss.
- The loss is weight duplication: each engine holds its own copy of the model, so the pair pays for the weights twice.

## Break-even against a shared card

- A slice costs 0.01188s per token whether or not the neighbour is active.
- A shared card costs 0.006496s idle and 0.01529s under the Track B HBM antagonist.
- Partitioning wins on latency only once the neighbour is contending more than 61% of the time under this two-state workload model.
