# 06 - Scheduler

[Index](README.md) | Prev: [05 - Market clock](05-market-clock.md) | Next: [07 - Expiration and discovery](07-expiration-and-discovery.md)

Code: `scheduler.py`, `main.py` | Tests: `tests/test_scheduler.py` (12 tests)

## Loop

```text
while not stopping:
    now  = clock now
    day  = New York date
    win  = session_window(day)
    if win is None or now > win.end:      -> finalise health, sleep to next session
    if now < win.start:                   -> sleep to the open
    boundary = next_boundary(now)         -> strictly future, epoch-aligned
    if boundary > win.end:                -> sleep past the close, re-evaluate
    sleep_until(boundary)
    run_cycle(...)  with poll_timestamp = boundary
```

The next boundary is always recomputed from the *current* time after a cycle
finishes.  Two consequences, both tested:

* **No drift** (I12): a cycle taking 20s still samples at :00 every minute.
* **No catch-up burst** (I13): a cycle taking 90s misses the next boundary and
  resumes at the following one instead of firing rapid back-to-back requests.

`collect(); sleep(60)` is explicitly *not* used - it drifts by the processing
time of every cycle, which would smear the sampling grid over a session.

## Sleeping and signals

`_sleep_until` sleeps in 1-second slices, checking the stop flag each time, so
SIGINT/SIGTERM are acted on within about a second even during an overnight wait.
`install_signal_handlers` registers both signals; `_handle_signal` only sets the
flag, so the in-flight cycle completes and the health file is written before the
process exits.  That is what makes `systemctl stop` and VM reboots safe.

`self.stopping` (a property) is used inside the loop rather than `self._stop`,
because mypy narrows the raw attribute after the `while not self._stop:` guard
and would mark the break as unreachable.

## Health lifecycle

`_ensure_health(day)` builds a `HealthTracker` for the trading date, calls
`restore()` to pick up counters written earlier the same day, and writes the
file immediately.  Thereafter the file is rewritten every `HEALTH_WRITE_EVERY`
(5) cycles so a watchdog sees live progress, and once more with
`finished=True` when the session ends or the process stops.

Restart behaviour: a collector restarted at noon reloads both the sticky
universe (from metadata JSON) and the health counters (from the health JSON), so
the day looks continuous rather than starting over at noon.

## Injection points

`CollectorScheduler(settings, client=..., collector=..., clock=..., now=..., sleep=...)`.
The tests pass a `FakeTime` whose `sleep` simply advances a virtual clock, so a
full trading day of scheduling logic is verified in milliseconds with no waiting
and no network.

## Entry point

`main.py`:

```bash
python main.py                 # run until stopped (systemd)
python main.py --max-cycles 3  # bounded first run
python main.py --once          # one cycle now, ignoring market hours
python main.py --log-level DEBUG --env-file /path/.env
```

Exit codes: `0` clean, `1` unhandled runtime failure, `2` configuration error.
`--once` calls `CollectorScheduler.run_immediate_cycle`, which is the supported
public API for a manual sample (it still writes and finalises health properly).

Related: [09 - Collection cycle](09-collection-cycle.md), [12 - Health, QA, watchdog](12-health-qa-watchdog.md)
