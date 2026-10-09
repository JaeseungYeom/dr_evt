# Job trace conversion, sampling, and synthetic generation

This directory is supported by six scripts:

1. `convert_job_stream_to_epoch.py` extracts Lassen `pbatch` jobs from the raw
   CSM allocation history, converts local timestamps to Unix epoch seconds,
   and can calculate job duration.
2. `sample_job_stream.py` selects a contiguous eligible historical workload
   window without separating its correlated job properties.
3. `downsample_job_stream.py` retains one unchanged row from each fixed-size
   block, reducing offered load while preserving the trace's time span.
4. `generate_synthetic_job_stream.py` preserves a historical sequence of
   submission times and node counts, then statistically samples durations and
   conditional time limits from a global or local population.
5. `../analyze_job_stream.py` calculates capacity and scheduling metrics from
   any completed trace containing submit, begin, end, and node-count columns.
6. `../convert_completed_trace_to_simulation.sh` converts such a completed
   trace into four-column DR_EVT simulation input.

All six scripts use Python's standard library and require Python 3.9 or
newer.

## Convert a completed trace to simulation input

The input and output locations are explicit arguments and are not hardcoded:

```bash
../convert_completed_trace_to_simulation.sh \
  /path/to/lassen_pbatch_job_stream_epoch.csv \
  /path/to/lassen_pbatch_simulation.csv
```

The output columns are `submit_time,num_nodes,time_limit,duration`, where
`duration` is `end_time - begin_time`. DR_EVT requires duration not to exceed
the submitted time limit, so the converter preserves observed duration and
sets an insufficient time limit to `ceil(duration)`.

## Analyze a completed trace

Pass the input path explicitly; the analysis script does not assume that a
trace exists at a repository-relative location:

```bash
python ../analyze_job_stream.py /path/to/lassen_pbatch_job_stream_epoch.csv
```

The script reports the largest single job and the peak number of concurrently
running nodes. By default, the peak concurrent value is used as the inferred
operational capacity. When an authoritative machine capacity is known, pass
it explicitly:

```bash
python ../analyze_job_stream.py /path/to/trace.csv --total-nodes 792
```

Utilization is total node-seconds divided by operational nodes times the
interval from the first submission through the final completion. Turnaround
is `end_time - submit_time`, duration is `end_time - begin_time`, and bounded
slowdown is `max(1, turnaround / max(duration, 10 seconds))`. The slowdown
bound can be changed with `--bounded-slowdown-threshold`.

> **Scope of these scripts:** `convert_job_stream_to_epoch.py` is a
> Lassen-specific adapter. It depends on Lassen CSM column names, selects the
> `pbatch` queue, interprets local timestamps in `America/Los_Angeles`, and
> uses the trace's approximate begin-time ordering to resolve the repeated
> Daylight Saving Time (DST) hour. The physical order of CSV columns does not
> matter because columns are
> read by header name. In contrast, `sample_job_stream.py` and
> `generate_synthetic_job_stream.py` are general-purpose components: they can
> process any job trace that provides the standardized columns documented
> below.

## 1. Convert the Lassen CSM allocation history

### Expected input

`convert_job_stream_to_epoch.py` is the Lassen-specific input adapter. Its
input is a CSV file such as `final_csm_allocation_history_hashed.csv`. Column
order does not matter, and additional columns are allowed, but these named
columns must exist:

| Column | Meaning |
| --- | --- |
| `job_submit_time` | Local job submission timestamp |
| `num_nodes` | Number of requested/allocated nodes |
| `begin_time` | Local execution start timestamp |
| `end_time` | Local execution end timestamp |
| `time_limit` | Job time limit in seconds |
| `exit_status` | Job exit status; zero represents success |
| `queue` | Queue name used to select `pbatch` jobs |

The timestamp fields are expected to resemble
`2020-11-01 01:07:31.70215` and must not contain a UTC offset. By default they
are interpreted in `America/Los_Angeles`.

The input should be approximately ordered by `begin_time`. This allows the
script to distinguish the first and second occurrence of times such as 01:30
when daylight saving time ends. Small out-of-order differences are tolerated.

### Processing logic

The script performs the CSV-aware equivalent of the original `awk` command:

- Select rows whose `queue` is exactly `pbatch`.
- Select and rename `job_submit_time` to `submit_time`.
- Retain `num_nodes`, `begin_time`, `end_time`, `time_limit`, and
  `exit_status`.
- Parse CSV quoting instead of deleting quote characters blindly.
- Convert local timestamps to Unix epoch seconds.
- Resolve the repeated fall-back hour so that
  `submit_time < begin_time < end_time`.
- Preserve fractional seconds without floating-point rounding.

### Outputs

The required output contains:

```text
submit_time,num_nodes,begin_time,end_time,time_limit,exit_status
```

With `--simulation-output`, a second file is produced containing:

```text
submit_time,num_nodes,duration,time_limit,exit_status
```

Here, `duration = end_time - begin_time`, in seconds. Timestamps are Unix
epoch seconds, and values may include a fractional part.

### Generate the standardized historical traces

```bash
./convert_job_stream_to_epoch.py \
  final_csm_allocation_history_hashed.csv \
  lassen_pbatch_job_stream_epoch.csv \
  --simulation-output lassen_pbatch_scheduling_simulation.csv
```

Use a different source timezone if necessary:

```bash
./convert_job_stream_to_epoch.py input.csv output.csv \
  --timezone America/Los_Angeles \
  --simulation-output simulation_input.csv
```

## 2. Sample or generate a job trace

Both tools use empirical records instead of assuming a parametric distribution
such as normal, exponential, or log-normal:

- `sample_job_stream.py` selects an intact contiguous historical window. Use
  it when temporal correlations and the original workload regime must remain
  unchanged.
- `generate_synthetic_job_stream.py` preserves a contiguous historical
  submission-time sequence, statistically samples `(num_nodes, duration)`
  pairs, then samples `time_limit` conditional on duration.

### Expected input: a general historical job trace

Both scripts accept a general CSV job trace with the following named columns.
Column order does not matter, and extra columns are ignored.

| Column | Requirement |
| --- | --- |
| `submit_time` | Numeric timestamp, normally Unix epoch seconds |
| `num_nodes` | Number of nodes associated with the job |
| `duration` | Positive runtime in seconds; fractions are supported. May be replaced by both `begin_time` and `end_time`. |
| `time_limit` | Positive time-limit value in seconds to sample |
| `exit_status` | Required only when `--successful-only` is used; zero means success |

Both `lassen_pbatch_scheduling_simulation.csv` and the completed
`lassen_pbatch_job_stream_epoch.csv` are directly usable as input. When
`duration` is absent, the scripts derive it as `end_time - begin_time`. Use
consistent units: duration and time limit should both be in seconds.

The scripts sort eligible input jobs by numeric `submit_time`; the original CSV
does not need to be pre-sorted.

### Time-limit handling, eligibility filters, and normalization

- `time_limit` is expressed in seconds, like `duration`; it is not a runtime
  multiplier or a value in minutes.
- `--max-time-limit SECONDS` applies the target platform's maximum time limit.
  A larger historical `time_limit` is set to this cap. If `duration` exceeds
  the platform cap, duration is capped as well. No platform maximum is applied
  when the option is omitted.
- After applying the optional platform maximum, an input job whose `duration`
  exceeds its submitted `time_limit` retains its observed duration and has its
  time limit extended to `ceil(duration)`. This matches
  `convert_completed_trace_to_simulation.sh`.
- `--min-duration SECONDS` removes jobs whose normalized duration is less than
  the given value. A job exactly equal to the threshold remains eligible.
- `--successful-only` removes jobs whose `exit_status` is not `0`.
- Without these options, all positive-duration jobs are eligible and the
  `exit_status` column is optional.

The filters define the eligible population before a workload window is
selected.

### Historical window sampling

`sample_job_stream.py` uniformly selects a valid starting index and takes `N`
consecutive eligible jobs in submit-time order. By default, each selected job
retains its `submit_time`, `num_nodes`, `duration`, and `time_limit`. Thus the
result preserves both the empirical distributions and their time-varying
correlations.

Its `--with-replacement` option is an explicit bootstrap alternative: complete
`(num_nodes, duration, time_limit)` records are resampled within the selected
window and assigned to that window's submission times. Complete records stay
together even in this mode.

### Statistical generation

`generate_synthetic_job_stream.py` constructs each trace in three steps:

1. Uniformly select a contiguous window and retain its ordered submission
   times.
2. Sample `(num_nodes, duration)` pairs from an empirical population. Keeping
   these values together preserves the relationship between requested size
   and runtime.
3. For each duration, sample a time limit from records in the same one-second
   duration bucket. If fractional durations make that limit too short, extend
   it to `ceil(duration)`.

`--time-binning work-cycle` partitions the population into three additional
submission-time regimes before steps 2 and 3: weekday work hours, weekday off
hours, and weekends. Times are interpreted using `--timezone` (default:
`America/Los_Angeles`). Weekday work hours default to 09:00 through 16:59 and
can be changed with `--work-hours-start` and `--work-hours-end`. The default
`--time-binning none` preserves the unpartitioned behavior.

`--sampling-scope global` is the general default and uses the complete
eligible trace for steps 2 and 3. It most closely matches trace-wide marginal
distributions. `--sampling-scope local` uses only the selected arrival window;
it is appropriate when durations and time limits have an evolving trend. In
both modes, `--with-replacement` controls pair resampling; sampling is without
replacement by default.

Lassen has a substantial temporal trend, so use `local` for its synthetic
traces. Global sampling remains available for experiments that intentionally
target whole-trace marginals; it can combine an active arrival/node-count
window with node-count/runtime pairs from a different historical regime. Even
local generation randomizes job ordering and can therefore change queueing
metrics. Use `sample_job_stream.py` when the historical temporal correlations
must be retained for scheduler comparisons.

### Expected output

```text
submit_time,num_nodes,time_limit,duration
```

The output has one header row followed by exactly `N` synthetic jobs.
Every newly generated row satisfies `duration <= time_limit`. Existing CSVs
are not rewritten automatically when the generator changes; regenerate older
outputs to apply this invariant.

### Generate one trace

The following statistically generates 100,000 jobs using local duration and
time-limit distributions from successful historical jobs that ran for at
least 60 seconds. Lassen's platform time-limit cap is 43,200 seconds (12
hours):

```bash
./generate_synthetic_job_stream.py \
  lassen_pbatch_job_stream_epoch.csv \
  synthetic_jobs_100000_min60s_successful.csv \
  100000 \
  --min-duration 60 \
  --max-time-limit 43200 \
  --successful-only \
  --sampling-scope local \
  --time-binning work-cycle
```

Use `./sample_job_stream.py` with the same positional arguments and filters to
select an intact historical window instead.

For reproducible results, provide a seed:

```bash
./generate_synthetic_job_stream.py historical.csv synthetic.csv 100000 \
  --min-duration 60 --max-time-limit 43200 --successful-only --seed 2026
```

Running the same command with the same input and seed produces identical
output. Without `--seed`, Python uses system randomness.

### Generate ten reproducible traces

```bash
for i in $(seq -w 1 10); do
  ./generate_synthetic_job_stream.py \
    lassen_pbatch_job_stream_epoch.csv \
    "synthetic_jobs_100000_min60s_successful_${i}.csv" \
    100000 \
    --min-duration 60 \
    --max-time-limit 43200 \
    --successful-only \
    --sampling-scope local \
    --time-binning work-cycle \
    --seed "$i"
done
```

Using the loop index as the seed makes every selected window reproducible.

### Reduce campaign load by a factor of four

To retain the first job from every four-row block in each 100,000-job trace:

```bash
python experimental/synthesize/lassen/downsample_job_stream.py \
  experimental/synthesize/lassen/synthetic_traces/100000/*.csv \
  --output-dir experimental/synthesize/lassen/synthetic_traces/25000 \
  --stride 4
```

Each output has the same filename and header as its input followed by 25,000
unchanged job rows. Submission timestamps are not compressed, so the offered
load is reduced over the original time interval. Use `--offset 1`, `2`, or `3`
to retain a different position within each four-row block. Existing output
files are protected unless `--overwrite` is supplied.

Run the knowledge-transfer campaign on these traces with a new output
directory and the matching validation count:

```bash
python experimental/multi-cluster/run_kt_scheduling_study.py \
  [the existing table and machine options] \
  --jobs-glob 'experimental/synthesize/lassen/synthetic_traces/25000/*.csv' \
  --jobs-per-trace 25000 \
  --output-dir /results/kt-scheduling-25000
```
