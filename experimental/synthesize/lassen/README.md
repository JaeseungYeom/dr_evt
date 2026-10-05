# Job trace conversion and synthetic trace generation

This directory contains two scripts:

1. `convert_job_stream_to_epoch.py` extracts Lassen `pbatch` jobs from the raw
   CSM allocation history, converts local timestamps to Unix epoch seconds,
   and can calculate job duration.
2. `generate_synthetic_job_stream.py` accepts a standardized historical job
   trace and generates a synthetic trace by sampling arrival times and job
   size/runtime pairs separately, then sampling each time limit conditionally
   on the selected runtime.

Both scripts use Python's standard library and require Python 3.9 or newer.

> **Scope of the two scripts:** `convert_job_stream_to_epoch.py` is a
> Lassen-specific adapter. It depends on Lassen CSM column names, selects the
> `pbatch` queue, interprets local timestamps in `America/Los_Angeles`, and
> uses the trace's approximate begin-time ordering to resolve the repeated
> Daylight Saving Time (DST) hour. The physical order of CSV columns does not
> matter because columns are
> read by header name. In contrast, `generate_synthetic_job_stream.py` is the
> general-purpose component: it can process any job trace that provides the
> standardized columns documented below.

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

## 2. Generate a synthetic job trace

> **Data-driven sampling:** This approach resamples directly from observed job
> records instead of assuming an arbitrary parametric distribution (for
> example, normal, exponential, or log-normal). As a result, the generated
> values follow the empirical behavior present in the historical data,
> including irregular shapes, heavy tails, and repeated values. The method
> preserves historical arrival patterns, keeps `num_nodes` paired with
> `duration`, and samples `time_limit` conditionally on the duration bucket.
> These independently sampled components intentionally do not preserve every
> possible correlation from the original trace.

### Expected input: a general historical job trace

`generate_synthetic_job_stream.py` is not tied to Lassen. It accepts a general
CSV job trace with the following named columns. Column order does not matter,
and extra columns are ignored.

| Column | Requirement |
| --- | --- |
| `submit_time` | Numeric timestamp, normally Unix epoch seconds |
| `num_nodes` | Number of nodes associated with the job |
| `duration` | Positive runtime in seconds; fractions are supported |
| `time_limit` | Positive time-limit value in seconds to sample |
| `exit_status` | Required only when `--successful-only` is used; zero means success |

The generated `lassen_pbatch_scheduling_simulation.csv` is directly usable as
this input. For another trace format, rename or derive its columns to match the
table above. If it contains start/end timestamps rather than duration, compute
`duration = end_time - begin_time` first. Use consistent units: duration and
time limit should both be in seconds.

The script sorts eligible input jobs by numeric `submit_time`; the original
CSV does not need to be pre-sorted.

### Time-limit handling, eligibility filters, and normalization

- `time_limit` is expressed in seconds, like `duration`; it is not a runtime
  multiplier or a value in minutes.
- `--max-time-limit SECONDS` applies the target platform's maximum time limit.
  A larger historical `time_limit` is set to this cap. The job remains
  eligible; if its `duration` exceeds the resulting limit, its duration is
  set to that limit. No platform maximum is applied when the option is omitted.
- After applying the optional platform maximum, an input job whose `duration`
  exceeds its effective `time_limit` is normalized by setting `duration` to
  `time_limit`.
- `--min-duration SECONDS` removes jobs whose normalized duration is less than
  the given value. A job exactly equal to the threshold remains eligible.
- `--successful-only` removes jobs whose `exit_status` is not `0`.
- Without these options, all positive-duration jobs are eligible and the
  `exit_status` column is optional.

The same eligible population is used by all three sampling stages.

### Synthetic sampling logic

For a requested count of `N` jobs, the script does the following:

1. **Submission times:** Uniformly select a valid starting index, take `N`
   consecutive eligible jobs in submit-time order, and retain only their
   `submit_time` values. This preserves a real historical arrival pattern and
   its interarrival gaps.
2. **Node count and duration:** Independently sample `N` eligible jobs
   uniformly from the whole eligible trace. Keep each selected job's
   `(num_nodes, duration)` pair together, preserving the historical
   relationship between job size and runtime. Sampling is without replacement
   by default; `--with-replacement` enables bootstrap sampling.
3. **Time limit:** Assign every eligible historical job to a one-second bucket
   using `ceil(duration)`. Thus `(0, 1]` is bucket 1, `(1, 2]` is bucket 2,
   and so on. For each synthetic job, uniformly select a `time_limit` from all
   eligible historical records in the sampled runtime's bucket. This is a
   conditional sample, not an independent sample from the trace-wide
   `time_limit` distribution. Repeated historical values remain repeated in
   the bucket and retain their empirical frequency. If the selected limit is
   slightly shorter than the sampled duration (possible when two fractional
   durations share a bucket), the output duration is capped at that selected
   limit.

The three stages are independent except that `num_nodes` and `duration` remain
paired and the time-limit bucket is selected from the sampled duration.

### Expected output

```text
submit_time,num_nodes,time_limit,duration
```

The output has one header row followed by exactly `N` synthetic jobs.
Every newly generated row satisfies `duration <= time_limit`. Existing CSVs
are not rewritten automatically when the generator changes; regenerate older
outputs to apply this invariant.

### Generate one trace

The following creates 100,000 jobs using successful historical jobs that ran
for at least 60 seconds. Lassen's platform time-limit cap is 43,200 seconds
(12 hours):

```bash
./generate_synthetic_job_stream.py \
  lassen_pbatch_scheduling_simulation.csv \
  synthetic_jobs_100000_min60s_successful.csv \
  100000 \
  --min-duration 60 \
  --max-time-limit 43200 \
  --successful-only
```

For reproducible results, provide a seed:

```bash
./generate_synthetic_job_stream.py historical.csv synthetic.csv 100000 \
  --min-duration 60 --max-time-limit 43200 --successful-only --seed 2026
```

Running the same command with the same input and seed produces identical
output. Without `--seed`, Python uses system randomness.

### Generate ten independent traces

```bash
for i in $(seq -w 1 10); do
  ./generate_synthetic_job_stream.py \
    lassen_pbatch_scheduling_simulation.csv \
    "synthetic_jobs_100000_min60s_successful_${i}.csv" \
    100000 \
    --min-duration 60 \
    --max-time-limit 43200 \
    --successful-only
done
```

Because no seed is supplied, each invocation uses fresh system randomness.
