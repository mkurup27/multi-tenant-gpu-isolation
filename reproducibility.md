# Reproducibility package

This manifest separates files suitable for a public article companion from private operational archives. The public set is what this repository contains; it accompanies the DigitalOcean Community article *Does dedicated GPU inference share resources? An isolation-boundary map with measurements*.

The article names the four measurements descriptively. The files here keep the working track letters: Track C is the seven-day run, Track B is the induced-contention runs, Track E is the self-contention cases, and Track G is the MIG and MPS cases.

## Pinned versions

Every track served the same model from the same container image:

- Model: `Qwen/Qwen3-8B`, Hugging Face revision `b968826d9c46dd6066d109eabc6255188de91218`
- Container: `vllm/vllm-openai@sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52`
- Driver: NVIDIA 580.173.02 on an H100 80 GB HBM3 in PCI passthrough, MIG disabled except during Track G

## Publish with the article

### Study design

- `experiments.md`
- `analysis-plan.md`
- `requirements.txt`
- `track_c.env.example`
- `intervention.env.example`

### Shared benchmark and telemetry

- `isolation_bench.py`
- `telemetry.sh`
- `preflight_gpu_host.sh`
- `preflight_control_node.sh`
- `test_isolation_bench.py`

### Track C: longitudinal observation

- `schedule_track_c.sh`
- `run_track_c_window.sh`
- `isolation-track-c.service`
- `validate_track_c.py`
- `analyze_track_c.py`
- `analysis/track_c_summary.md`
- `analysis/track_c_windows.csv`
- `analysis/track_c_analysis.json`

### Track B: induced contention

- `antagonists.py`
- `intervention_control.sh`
- `generate_track_b_schedule.py`
- `run_track_b.sh`
- `analyze_track_b.py`
- `test_track_b_orchestration.py`
- `provenance/track_b_schedule.csv`
- `provenance/track_b_schedule.sha256`
- `analysis/track_b_effects.csv`
- `analysis/track_b_signatures.md`

The frozen Track B schedule SHA-256 is:

```text
75310e0fad91d22086ef52335f59e51f8afaa03168c11c31aaaa55232143052d
```

### Track E: self-inflicted contention

- `run_track_e.sh`
- `analyze_track_e.py`
- `analysis/track_e_cases.csv`
- `analysis/track_e_summary.md`

### Track G: MIG and MPS

- `mig_control.sh`
- `run_track_g.sh`
- `analyze_track_g.py`
- `mps_control.sh`
- `run_track_mps.sh`
- `analyze_track_mps.py`
- `analysis/track_g_summary.md`
- `analysis/track_mps_summary.md`

### Cost model

- `cost_model.py`

## Retain privately

Do not publish these without a separate privacy and size review:

- `track-c-2026-09-15.tar.gz`
- `intervention-experiments-2026-09-10.tar.gz`
- `track-c-extracted/`
- `results/`
- populated files from `/etc/isolation/`
- SSH private keys, API keys, tokens, or shell history
- raw host logs containing IP addresses, UUIDs, process details, or account metadata

The private archives are evidence and recovery copies, not the public harness. Their verified SHA-256 values are:

- Track C: `88995593d6d1ca4716663bee87ec43a3595abe2162351ccb78e738b19316a293`
- Intervention tracks: `f5a2ea3511c0de91158dc492615fcffcd067eb19b060fbec58d4a1f1b475b9f8`

## Publication checklist

1. Publish only the files in the public set.
2. Confirm no populated environment file or credential is present.
3. Keep the raw archives until the article has passed technical review.
