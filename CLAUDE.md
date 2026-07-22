# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TooGoodToMiss** is a Telegram bot that monitors TooGoodToGo (TGTG) favorite magic bags and notifies the user when items become available. It runs as a single long-lived process (`python -m app.main`), deployed to a VPS via `scripts/deploy.sh` and kept alive in a `tmux` session. Python 3.10+, managed locally with a venv.

## Behavioral Guidelines (Karpathy / Multica)

Bias toward **caution over speed**. For trivial tasks, use judgment. **Project rules below override these** when they conflict.

### 1. Think Before Coding

- **State assumptions explicitly.** If multiple interpretations exist, present them — don't pick silently.
- **If unclear, stop and ask.** Don't hide confusion behind plausible-looking code.
- **If a simpler approach exists, say so.** Push back when warranted.

### 2. Simplicity First

- Minimum code that solves the problem. No speculative features, no abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested. No error handling for impossible scenarios.
- If you wrote 200 lines and 50 would do, rewrite.

### 3. Surgical Changes

- Touch only what the task requires. Don't "improve" adjacent code, comments, or formatting.
- Match existing style even if you'd do it differently.
- If you spot unrelated dead code, **mention it — don't delete it**.
- Only clean up imports/symbols _your own_ changes orphaned.
- Every changed line must trace directly to the user's request.

### 4. Goal-Driven Execution

Transform tasks into verifiable goals **before** coding:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step work, state a brief plan with per-step verification.

## Commands

**Environment**: local venv (`requirements.txt`).

```bash
# Setup
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Run locally
python -m app.main

# Tests
python -m pytest tests/ -q
python -m pytest tests/test_scheduler.py -q                    # single file
python -m pytest tests/test_scheduler.py::TestScheduler::test_name -q  # single test
python -m pytest --cov=app --cov-report=term

# Deploy to the VPS (requires .env at repo root — VPS_USER/VPS_HOST/VPS_BOT_PATH/SSH_KEY)
./scripts/deploy.sh
```

## Architecture

### Single process, one entry point

`app/main.py` builds a single `python-telegram-bot` `Application` and runs it with `run_polling()` — there is no webhook, no API Gateway, no separate scheduler/monitoring/webhook functions. `build_application()` wires `StateStore`, `Scheduler` and `TelegramBotHandler`, then arms the first monitoring pass via `_arm_monitoring(application.job_queue, 0)`.

### The self-rescheduling monitoring job (the non-obvious part)

Monitoring is not a fixed interval. `monitor_job()` (`app/main.py`) runs one pass, then — **in a `finally` block** — computes `Scheduler.next_delay_seconds()` and re-arms itself via `_arm_monitoring()`. This is deliberate: `monitor_job` is the *only* thing that reschedules monitoring, so no code path may return from it without arming a successor. If `next_delay_seconds()` itself raises, the `finally` block falls back to `Scheduler.OFF_WINDOW_RETRY_MINUTES * 60` rather than leaving the loop unarmed.

`next_delay_seconds()` (`app/core/scheduler.py`) **never returns `None`**: it is the sole re-arming mechanism, so `None` would stop monitoring for good. Randomized delays (10–20 min in the 10:00–12:00 window, 2–5 min in the 12:00–19:00 window, idle on Sunday) exist to defeat TGTG anti-bot fingerprinting — do not replace them with a fixed interval.

**At most one monitoring job at any time** is an invariant, not an accident: `_arm_monitoring()` cancels every pending job named `"monitoring"` before scheduling a new one, specifically to cover a caller (e.g. `/wakeup`) racing an in-flight pass that will also re-arm in its own `finally`. Two concurrent chains would double the TGTG call cadence and defeat the anti-fingerprinting delays. **Any new code path that needs to (re)arm monitoring must call `_arm_monitoring()` — never `job_queue.run_once(monitor_job, ...)` directly.**

Every `run_once()` call — inside `_arm_monitoring()` — passes `job_kwargs={"misfire_grace_time": None}`. Without it, APScheduler silently drops a job that fires more than ~1 second late (e.g. the process was momentarily busy), and since `monitor_job` is the only re-arming mechanism, **the monitoring loop stops for good, with nothing in the logs to flag it**. This is the project's #1 failure mode — don't add a `run_once` for this job without it.

### `state.json` is the single source of runtime state

`StateStore` (`app/core/state.py`) is the single source of runtime state — TGTG session, cooldown end time, user language, notification de-dup — persisted to `state.json` at the repo root (path overridable via `STATE_FILE`). Every mutation writes atomically (temp file + `os.replace`) so a crash mid-write never truncates it. The file is gitignored and **`scripts/deploy.sh` never pushes it** to the VPS.

**The one-way seed trap**: `StateStore._seed_from_env()` reads `ACCESS_TOKEN` / `REFRESH_TOKEN` / `TGTG_COOKIE` / `LAST_TIME_TOKEN_REFRESHED` / `USER_LANGUAGE` from the environment, but only the **first time** `state.json` doesn't exist yet. After that, `state.json` is authoritative — editing `.env` and redeploying has **no effect** while it's present on the VPS. This is deliberate: overwriting a live TGTG session with a stale local copy on every deploy would force a re-login and trigger a CAPTCHA. To force new tokens, delete the file on the VPS and redeploy:

```bash
ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"
./scripts/deploy.sh
```

(Same warning lives in `scripts/deploy.sh`, `README.md` and the `StateStore.get_tgtg_credentials()` docstring — keep all four in sync if this changes.)

### Cooldown / anti-bot flow

TGTG CAPTCHA → `TgtgService` detects `"captcha"` in the error string → raises `ForbiddenError` → `TgtgServiceMonitor._monitor_favorites()` calls `scheduler.activate_cooldown()` (default 30 min) → `state.json`'s `cooldown_end_time` is set → both `Scheduler.should_monitor_now()` and `next_delay_seconds()` short-circuit until it expires. The user can pause/resume manually from Telegram (`/pause`, `/wakeup`, `/status`).

### Notification de-duplication

`StateStore` keeps a `notifications` map (`store_id` → last-notified date, UTC) in `state.json`, pruned to today's entries on every write. `TgtgService.get_notification_messages()` emits a message only when `items_available > 0` **and** `StateStore.was_notified_today()` is false for that store.

### Vendored TGTG client

`app/services/tgtg_service/tgtg_client.py` is a **vendored fork** of the `tgtg` PyPI package (the dependency was deliberately removed — see commits `7600589` / `1bb5131`). It owns login-by-email polling, token refresh, and the `datadome` cookie. When TGTG changes its API (endpoint versions like `item/v8/`, `auth/v5/`, or headers), patch this file — there is no upstream to bump.

### Logging silences third-party loggers on purpose

`app/common/logger.py` sets `httpx`, `httpcore`, `telegram` and `apscheduler` to `WARNING`. `httpx` logs the full request URL at INFO on every polling call, including `https://api.telegram.org/bot<TOKEN>/getUpdates` — and unlike CloudWatch, `logs/app.log` is a persistent file on the VPS. **Never raise these loggers back to INFO without first masking the token.**

### Layers

`app/core/` — `scheduler.py` (delay/window logic), `state.py` (`state.json` persistence), `telegram_bot_handler.py` (python-telegram-bot wiring).
`app/services/` — domain (`tgtg_service_monitor.py` orchestrates a monitoring run, `tgtg_service/` holds the client, pydantic `models.py` for the TGTG payload, and `notification_formatter.py`).
`app/common/` — `utils.py` (env vars, Telegram send, localization), `constants.py`, `localizable.json`, `logger.py`.

### Telegram bot

`TelegramBotHandler` wraps python-telegram-bot's `Application` and runs continuously via `run_polling()` — no per-request `initialize()`/`shutdown()`, no webhook. Per-user state (language, cooldown) goes through `StateStore`, not env vars.

All user-facing strings live in `app/common/localizable.json` under `en` / `fr` — add a key to **both** locales; `Utils.localize()` returns `""` (silently blank message) on a missing key. Every command has a paired inline button; adding a command means touching `_register_handlers`, `_callback_query_handler`, `_set_bot_commands`, and both locale blocks.

## Conventions

- Multi-line signature style (one parameter per line) is used across the codebase — match it.
- Logging via `app.common.logger.LOGGER` only. Existing logs print raw tokens/credentials at INFO; don't add new ones.
- Errors: custom exceptions in `app/services/tgtg_service/exceptions.py`. `_monitor_favorites` is the single place that maps exception type → user-facing Telegram message + cooldown decision.
- Tests use `unittest.TestCase` classes with `unittest.mock`, `freezegun` for time, and shared fixtures in `tests/conftest.py`. Test files mirror the module they cover.
