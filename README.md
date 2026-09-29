# GPU Isolation Experiment Harness

Reproducible tooling for measuring tenant-visible variability and controlled contention in single-GPU LLM inference.

The harness accompanies the DigitalOcean Community article *Does dedicated GPU inference share resources? An isolation-boundary map with measurements*, a study of one NVIDIA H100 80 GB GPU Droplet running vLLM with `Qwen/Qwen3-8B`. It separates four questions. The article refers to each by a descriptive name; the code and analysis files keep the working track letters used during the study.

| Track | Name in the article | Question |
| --- | --- | --- |
| C | the seven-day run | What varies over seven days on an untouched GPU Droplet? |
| B | the induced-contention runs | What signatures do known GPU, host, network, storage, and power pressure produce? |
| E | the self-contention cases | What happens when queueing, mixed prompt lengths, or attempted KV-cache pressure come from the workload itself? |
| G | the MIG and MPS cases | How do hardware partitioning and process scheduling change isolation, latency, throughput, and KV-cache capacity? |

## What this repository can establish

The observational track measures performance visible to one tenant. It cannot identify another tenant or prove that an excursion was caused by a neighbor.

The intervention tracks create known pressure on a separate disposable GPU. Similarity to those signatures is diagnostic evidence, not causal attribution.

Read [`analysis-plan.md`](analysis-plan.md) before interpreting results and [`experiments.md`](experiments.md) before running anything.

## Repository contents

### Shared harness

- `isolation_bench.py` — streaming benchmark and window summaries
- `telemetry.sh` — GPU, CPU, disk, and network sampling
- `preflight_gpu_host.sh` and `preflight_control_node.sh` — environment checks
- `requirements.txt` — Python dependencies

### Track C

- `schedule_track_c.sh`
- `run_track_c_window.sh`
- `isolation-track-c.service`
- `validate_track_c.py`
- `analyze_track_c.py`

### Track B

- `antagonists.py`
- `intervention_control.sh`
- `generate_track_b_schedule.py`
- `run_track_b.sh`
- `analyze_track_b.py`

### Track E

- `run_track_e.sh`
- `analyze_track_e.py`

### Track G

- `mig_control.sh`
- `run_track_g.sh`
- `analyze_track_g.py`
- `mps_control.sh`
- `run_track_mps.sh`
- `analyze_track_mps.py`

The complete publication inventory and private-data exclusions are in [`reproducibility.md`](reproducibility.md).

## Requirements

The complete study used:

- NVIDIA H100 80 GB GPUs
- NVIDIA driver 580.173.02
- Docker with NVIDIA Container Toolkit
- vLLM container image `vllm/vllm-openai@sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52`
- `Qwen/Qwen3-8B` at Hugging Face revision `b968826d9c46dd6066d109eabc6255188de91218`
- Python 3 with the packages in `requirements.txt`
- a separate same-VPC controller
- Linux tools including `sysstat`, `fio`, `stress-ng`, `iproute2`, and `rsync`

MIG, MPS, power-limit, and antagonist commands must run only on a disposable intervention GPU. Never run them on a production host or the longitudinal Track C host.

## Local validation

Create an isolated Python environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the local tests:

```bash
python -m unittest test_isolation_bench.py test_track_b_orchestration.py
```

Check shell syntax:

```bash
for script in *.sh; do bash -n "$script"; done
```

## Reproducing the study

Do not begin with an experiment command. Follow [`experiments.md`](experiments.md) in order:

1. Freeze and checksum the analysis plan.
2. Record hardware, driver, model revision, and container-image provenance.
3. Calibrate load below saturation.
4. Run Track C without interventions.
5. Run Tracks B, E, and G on a separate disposable H100.
6. Validate completeness before inspecting or analyzing production results.
7. Archive and checksum raw data before destroying infrastructure.

Example environment files contain placeholders only:

- [`track_c.env.example`](track_c.env.example)
- [`intervention.env.example`](intervention.env.example)

Never commit populated environment files, private SSH keys, API tokens, or account-specific host metadata.

## Retained processed results

The `analysis/` directory contains publication-sized outputs:

- the full 336-window Track C series and day-block bootstrap summary
- Track B treatment effects and signature summary
- Track E per-case and null-result summaries
- MIG and MPS summaries

Raw request streams, host logs, and private archives are intentionally excluded from Git. Their checksums and retention policy are documented in `reproducibility.md`.

## Frozen Track B schedule

The exact randomized intervention schedule is stored under `provenance/`.

SHA-256:

```text
75310e0fad91d22086ef52335f59e51f8afaa03168c11c31aaaa55232143052d
```

## Safety and cost

This harness can:

- create sustained GPU, CPU, disk, and network load
- change GPU power limits
- enable and disable MIG
- start NVIDIA MPS
- consume expensive GPU infrastructure for days

Use disposable resources, configure billing alerts, retain cleanup traps, and verify that no GPU process remains after each intervention. Review every command before running it.

## License

No license has been selected. Add an approved license before making the repository public.
