# 20 - Build history

[Index](README.md) | Prev: [19 - Limitations](19-limitations-and-future-work.md)

How this implementation was actually carried out, including the mistakes, so a
future agent does not repeat the dead ends.

## Starting point

An almost empty repository: a `main.py` stub printing "Hello", a minimal
`pyproject.toml` (name `tradieroptions`), a `.gitignore`, a `README.md`, and an
existing `.env` using `TRADIER_API_KEY`. One commit ("first commit"). Python
3.11.9 on PATH, `.python-version` pinning 3.12, `uv` available.

## Order of construction

1. Environment: `uv venv --python 3.12`, then dependencies.
2. `config.py` - typed settings first, because everything else takes `Settings`.
3. `tradier_client.py` - the only module that talks to the network.
4. `schemas.py`, `normalization.py` - the data contract.
5. `market_clock.py` - verified interactively against real 2026 sessions
   (2026-09-15 regular, 2026-11-27 early close, 2026-09-12 weekend).
6. `expiration.py`, `strike_selector.py`, `contract_tracker.py`, `discovery.py`.
7. `storage.py` - atomicity and dedupe, sanity-checked with a scratch write.
8. `collector.py`, `cycle.py`, `health.py`, then `scheduler.py` and `main.py`.
9. Tools: `qa_report.py`, `watchdog.py`, `validate_dataset.py`, `backup.py`,
   `scripts/smoke_test_tradier.py`.
10. Tests per module, run immediately after each was written.
11. Docs, systemd units, then the full `pytest` / `ruff` / `mypy` sweep.

## Mid-build change of direction

Dependency management started as `requirements.txt` plus `requirements-dev.txt`.
The owner asked mid-build for `uv add`, so both files were removed and the
dependencies re-declared through `uv add` and `uv add --dev`, producing
`pyproject.toml` plus `uv.lock` (decision D20). `tenacity` was later dropped (D5).

## Bugs found and fixed during the build

| Bug | Symptom | Fix |
| --- | --- | --- |
| `largest_gap_minutes` with a single observation | `max() iterable argument is empty` when writing health on a non-session day | `default=0.0` on the `max` |
| Quadratic QA fixtures | The QA test module exceeded a 120s timeout | Build synthetic days with one batched write, with a comment explaining why |
| Test asserted the wrong dropped symbol | The chosen symbol sat outside the ATM window, so "missing" was 0 | Drop a symbol that is genuinely in the window |
| Immediate-cycle test expectations | A Saturday run has no 0DTE, so zero option rows is correct | Assert skip semantics rather than row counts |
| mypy narrowing in the scheduler | `if self._stop: break` flagged unreachable | Use the `stopping` property |
| Frozen dataclass versus Protocol | `Settings` fields are read-only | Declare `ClockSettings` members as read-only properties |

## Verification at completion

`pytest -q` 195 passed; `pytest --cov` 92%; `ruff check .` clean;
`mypy .` clean (strict) across 40 source files. Ruff fixes were applied with
`--fix`, then the remaining findings fixed by hand (line lengths, `SIM102`,
`SIM105`, `C416`); `PTH105` is ignored project-wide with a comment because
`os.replace` is used deliberately for atomic replacement and is spied on in a
test.

## Environment quirks worth knowing

* The shell used during the build truncated very long commands, so files were
  written in chunks; that is a tooling artefact, not a project constraint.
* Windows development, Linux deployment: paths go through `pathlib`, and the
  atomic-replace test asserts same-directory temp files, which keeps both
  platforms honest.
* A repository hook required a facts preamble before file writes during this
  session; irrelevant to the code, but it explains the commit-free build style.

## Production handoff and first live day

The target was changed from the initially considered Google e2-micro to an
Oracle Cloud Always Free Ampere A1 Oracle Linux VM. `git`, uv and the repository
were installed under `/opt/tradier-0dte-collector`. The initial systemd launch
failed because `.venv/bin/python` resolved through the user's home-managed uv
Python; an app-local `.uv-python` installation fixed that while retaining
`ProtectHome=true`.

The live smoke test then succeeded for SPY, QQQ and IWM. On 2026-09-17 the
service collected a complete set of artefacts and end-of-day structural QA
passed, with 405/406 timestamps for each ticker. That evidence uncovered the
sub-second opening-boundary scheduler race documented in [06 - Scheduler](06-scheduler.md).
The local correction and its focused scheduler test pass, but it has not yet
been committed, pushed, or deployed as of this handoff.

Off-VM backup, desktop catch-up synchronization, and locally calculated Greeks
were intentionally deferred until the collector has accumulated several more
live days. See [13 - Validation and backup](13-validation-and-backup.md) and
[19 - Limitations](19-limitations-and-future-work.md).
