# Preregistered analysis plan: lean GPU-isolation study

Freeze this file before the first production window. Record its SHA-256 in
`preregistration.sha256`. Changes after data collection begins must be appended
as dated amendments; do not rewrite the original decision.

## Scope and claims

The design is observational. It measures tenant-visible temporal variability
on one H100 GPU Droplet and, if enabled, managed endpoints. It does not observe
neighbors and cannot attribute an excursion to another tenant. Induced
contention on a separate H100 builds a diagnostic signature library; matching a
signature is suggestive, not causal proof.

Cross-tier absolute latency or throughput comparisons are allowed only when
summary files carry the same non-default `comparison_group`, meaning model,
revision, region, prompt set, output policy, and offered arrival rate were all
matched. Otherwise each managed tier is analyzed only against its own history.

## Experimental unit and endpoints

The experimental unit is one scheduled host × 30-minute time block. Requests
inside a window are subsamples used to estimate that window; they are not
independent experimental replicates.

Primary endpoint:

- window-level TTFT p99, in seconds

Secondary endpoints:

- window-level TTFT p50 and coefficient of variation
- request error and admission-drop rates
- completed output-token throughput for the self-hosted arm
- end-to-end p99
- time per output token (TPOT), calculated only when usage token counts exist
- CPU steal-time p95 and maximum
- GPU SM clock, power, temperature, and utilization distributions
- vLLM waiting-request gauge maximum and preemption-counter delta

Inter-chunk latency is exploratory. It must not be called inter-token latency.
TPOT is the decode-cadence metric used for publication; records without usage
token counts have no TPOT rather than a chunk-count substitute.

## Sampling and exclusions

- Schedule one synchronized window every 30 minutes for 14 complete UTC days.
- Use a 30-second warm-up admission interval followed by a 120-second measured
  admission interval. Warm-up requests are never included in measured metrics.
- Stop new admissions at the measured deadline and allow admitted requests to
  drain. Report both admission duration and completion span.
- Exclude a window only for a preregistered operational reason: controller
  restart, endpoint authentication/configuration error, missing raw file,
  telemetry process failure, or a deliberate intervention.
- Never exclude a valid slow request or window because it is an outlier.
- Report every exclusion by run ID and reason.
- A managed arm with client admission drops is overloaded and is not a valid
  steady-state latency window.

## Estimation

Report the empirical distribution of window-level endpoints. For each tier,
report median, p90, p95, and p99 across windows, plus 95% uncertainty intervals
from a moving-block bootstrap over complete UTC days. Resample days, not
requests, to preserve within-day autocorrelation. With only 14 days, label tail
intervals as imprecise and publish the full window series.

Estimate hour-of-day effects by comparing each UTC hour with that tier's
overall median, using day as the repeated block. Treat these as descriptive;
do not run 24 uncorrected significance tests.

The pilot is a feasibility check, not a power calculation. After 48 hours,
verify request counts, admission-drop rate, telemetry completeness, and whether
the configured rate is below saturation. Do not change duration or arrival rate
mid-study unless the study is restarted and the earlier windows are labeled
pilot data.

## Minimum meaningful effect

No equivalence or “indistinguishable” claim is permitted until the stakeholder
fills and signs these margins before production data are inspected:

- TTFT p99 relative margin: `10%`
- TTFT p99 absolute margin: `0.2 seconds`
- throughput relative margin: `10%`
- approver and UTC date: `Mani 2026-09-04T06:31:44Z`

Without signed margins, report estimates and uncertainty only.

## Induced-contention study

Use a separate intervention H100. Randomize treatment order with a recorded
seed; block by repeat; interleave zero-intensity controls; and retain the full
schedule. Each treatment gets at least three repeats. Start each victim request
from the remote NYC2 controller so network treatments affect the measured path.
Use a dedicated same-VPC iperf3 server, never a public test service.

MPS and MIG are separate exploratory arms. Run neither unless a disposable
preflight confirms guest permission to enable/disable MIG, reset the GPU, and
change its power limit. For MPS, start the daemon before both victim and
antagonist CUDA contexts. For MIG, compare two simultaneous partitions against
an undivided-GPU control at equal aggregate offered load.

## Amendments

Appended after production collection began. The decisions above are unchanged.

### A1 — collection shortened to 7 days (2026-09-07)

Production Track C collection runs 7 complete UTC days from
`2026-09-07T05:00:00Z`, not 14. Reason: publication deadline. Consequence: the
moving-block bootstrap resamples 7 days rather than 14, so every tail interval
is wider and the day-to-day term is estimated from half the blocks. Hour-of-day
comparisons rest on 7 observations per hour. Report p99 window-level intervals
as imprecise throughout and make no equivalence claim on the tail.

### A2 — host maintenance windows are an exclusion reason (2026-09-08)

`apt-daily-upgrade.timer` and `needrestart` run daily on every host in this
study, including the measured GPU Droplets. On the intervention controller on
2026-09-08 this swept the running job into a service restart at 06:16:37Z and
06:16:57Z, and restarted cron, ssh, rsyslog, polkit, multipathd, udisks2 and the
systemd daemons alongside it.

This is self-inflicted host load on the measured host and is not tenant
contention. Adding it to the preregistered operational exclusion list in
*Sampling and exclusions*:

- Any window overlapping an `apt-daily`, `apt-daily-upgrade`,
  `unattended-upgrades` or `needrestart` execution on the measured host or its
  controller is excluded, by run ID and with the responsible timestamp recorded.

The timers were masked on all four hosts on 2026-09-09, so this applies only to
windows recorded before that.

Outcome, resolved 2026-09-09 from `apt-daily.service`, `apt-daily-upgrade.service`
and `/var/log/apt/history.log` on both Track C hosts:

- Twelve maintenance executions occurred during collection, six per host, each
  lasting between 1 and 20 seconds.
- No window overlaps any of them. The narrowest margin is on `mk-test-droplet`,
  where window `20260907T223000Z` produced its final telemetry sample at
  22:32:35Z and `apt-daily.service` started at 22:32:52Z, 17 seconds later.
- No package was installed or upgraded on either host after 2026-09-05, before
  production collection began on 2026-09-07. The NVIDIA driver, kernel and
  container runtime were unchanged for the entire seven days, so the days are
  mutually comparable and no version break needs reporting.

**Zero windows are excluded under this amendment.** It is retained as a record
of the check, not as an active exclusion. The exposure was real and would have
mattered had the timing differed; the finding is that it did not.

### A3 — Track B ran in two separated blocks (2026-09-08)

An orchestration failure split the 84-treatment schedule. Treatments 001–007 ran
2026-09-08T06:00Z–06:15Z; treatments 008–084 ran 2026-09-08T14:47Z–20:10Z. The
randomized order is intact, since the resume logic replayed the frozen schedule
in sequence, and no treatment was reordered, repeated or dropped.

The 8.5-hour gap is a nuisance factor, not a treatment. The intervention host is
itself a multi-tenant Droplet, so its own baseline may differ between the two
blocks. Therefore:

- Estimate every treatment effect against zero-intensity controls from the same
  block, not against a pooled control mean across both blocks.
- Treatments 001–007 are compared against controls 001 and 006.
- Report the two block baselines separately. If they differ by more than the
  TTFT p99 margin in *Minimum meaningful effect*, say so and treat cross-block
  magnitude comparisons as descriptive only.

Outcome of the block-comparability check this amendment required: block 1
baseline TTFT p99 is 0.0335s against block 2's 0.0315s, a 6% difference and
inside the 10% margin in *Minimum meaningful effect*. TPOT p50 (0.00650s) and
throughput (524.4 tok/s) are identical to four significant figures. The two
blocks are comparable, so cross-block magnitude comparisons are permitted.
Effects are still estimated within block, as specified above.

Harness provenance: treatments 001–007 ran under the pre-fix `run_track_b.sh`
and 008–084 under the post-fix version. The changes were confined to the abort
and teardown path, and the `intervention_control.sh` change touched only the
`storage` branch, which no treatment in 001–007 used. The two blocks are
measurement-identical.

### A4 — the intensity factor did not modulate SM or HBM contention (2026-09-09)

For the `sm` and `hbm` layers, intensities 0.25, 0.5 and 1.0 produced
indistinguishable effects (TTFT p50 ratios 1.92/1.92/1.93 and 1.94/1.90/1.91
respectively). The antagonist saturates the resource at the lowest setting, so
for these two layers the design yields a present/absent contrast, not a
dose-response curve. Do not describe SM or HBM results as graded, and do not
interpret the flat intensity profile as evidence of a saturating hardware
response, because the antagonist and the resource are confounded here.

`cpu`, `storage` and `network_delay` did produce graded host-side responses and
may be described as dose-responsive. `power` has a threshold rather than a
gradient: 0.25 and 0.5 leave latency within noise while 1.0 moves it sharply.

## Missing data and multiplicity

Publish the expected and observed window counts per tier. Do not impute missing
windows. The TTFT p99 endpoint is primary; all other tests are secondary or
exploratory. If inferential tests are added across multiple treatments, control
the false-discovery rate within each endpoint family and document the method in
an amendment.
