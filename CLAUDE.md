# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TooGoodToMiss** is a serverless Telegram bot that monitors TooGoodToGo (TGTG) favorite magic bags and notifies the user when items become available. It runs as three AWS Lambda functions deployed with the Serverless Framework, backed by DynamoDB and EventBridge. Python 3.10 (Lambda runtime), managed locally with conda.

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

**Environment**: conda env named `TooGoodToMiss` (defined in `environment.yml`).

```bash
# Setup
conda env create -f environment.yml
conda activate TooGoodToMiss

# Tests (must run inside the conda env — freezegun/pytest-freezegun are not in base)
conda run -n TooGoodToMiss python -m pytest tests/ -q
conda run -n TooGoodToMiss python -m pytest tests/test_scheduler.py -q                    # single file
conda run -n TooGoodToMiss python -m pytest tests/test_scheduler.py::TestScheduler::test_name -q  # single test
conda run -n TooGoodToMiss python -m pytest --cov=app --cov-report=term

# Deploy (requires .env at repo root — serverless-dotenv-plugin loads it)
serverless deploy --stage dev
serverless deploy function --function tooGoodToMissMonitoring   # single function, faster
serverless logs -f tooGoodToMissMonitoring -t

# Rebuild + publish the Lambda layer (only when lambda_layer/requirements_layer.txt changes)
cd lambda_layer && mkdir -p python
pip install --platform manylinux2014_x86_64 --target=python --implementation cp \
    --python-version 3.10 --only-binary=:all: -r requirements_layer.txt
zip -r lambda_layer.zip python/
aws lambda publish-layer-version --layer-name TooGoodToMissLayer \
    --zip-file fileb://lambda_layer.zip --compatible-runtimes python3.10
```

⚠️ `serverless.yaml` pins the layer ARN to a **hardcoded version** (`:3`) on all three functions. Publishing a new layer version requires bumping that number in all three places, otherwise deployed code runs against stale dependencies.

Note: `tests/test_handlers.py::test_tgtg_monitoring_handler_valid_event` and `::test_telegram_webhook_handler` currently fail on `main` — pre-existing, not caused by your change.

## Architecture

### Three Lambdas, one entry module

All handlers live in `app/handlers.py`:

| Handler | Lambda | Trigger |
|---|---|---|
| `lambda_scheduler` | `too-good-to-miss-scheduler` | fixed cron `*/3 10-19 MON-SAT` |
| `tgtg_monitoring_handler` | `too-good-to-miss-monitoring` | EventBridge rules created at runtime |
| `telegram_webhook_handler` | `too-good-to-miss-telegram-webhook` | API Gateway `POST /` |

### The self-scheduling loop (the non-obvious part)

The monitoring Lambda is **not** on a fixed schedule. `Scheduler.schedule_next_invocation()` (`app/core/scheduler.py`) creates a **one-shot EventBridge rule** named `TooGoodToGo_monitoring_invocation_rule_<YYYYMMDDHHMM>` at a randomized future time, then deletes past-due rules on the next pass. Randomized delays (10–20 min in the morning window, 2–5 min in the afternoon) exist to defeat TGTG anti-bot fingerprinting — do not replace them with a fixed cron.

`tgtg_monitoring_handler` guards on `event['resources']` matching `MONITORING_EVENT_PATTERN`, so it ignores any event that didn't come from one of those generated rules. Rule name ↔ pattern prefix must stay in sync: `SCHEDULE_RULE_NAME_PREFIX` (`app/common/constants.py`) and `MONITORING_EVENT_PATTERN` (`app/handlers.py`) are two copies of the same string.

### Lambda env vars are mutable runtime state

There is no secrets store or state table for credentials — the app rewrites its own Lambda configuration via `Utils.update_lambda_env_vars()` (`lambda:UpdateFunctionConfiguration`):

- `ACCESS_TOKEN` / `REFRESH_TOKEN` / `TGTG_COOKIE` / `LAST_TIME_TOKEN_REFRESHED` — rotated by `TgtgServiceMonitor.update_credentials_env_vars()` whenever `TgtgClient` refreshes the TGTG session. Written on the **monitoring** Lambda.
- `COOLDOWN_END_TIME` — the pause flag. Written on the **monitoring** Lambda, read by both `Scheduler.is_bot_paused()` and `schedule_next_invocation()`.
- `USER_LANGUAGE` — written on the **telegram-webhook** Lambda by the language selector.

Consequence: a `serverless deploy` overwrites these with the values from `.env`, resetting live tokens and cooldown. Prefer `deploy function` for code-only changes, and be aware env-var writes are cross-function (the webhook Lambda mutates the monitoring Lambda's config).

### Cooldown / anti-bot flow

TGTG CAPTCHA → `TgtgService` detects `"captcha"` in the error string → raises `ForbiddenError` → `TgtgServiceMonitor._monitor_favorites()` calls `scheduler.activate_cooldown()` (default 30 min) → `COOLDOWN_END_TIME` set → both the scheduler and the monitoring handler short-circuit until it expires. The user can pause/resume manually from Telegram (`/pause`, `/wakeup`, `/status`).

### Notification de-duplication

DynamoDB table `UserNotifications` (`storeId` HASH, `lastNotificationDate` RANGE). `TgtgService.get_notification_messages()` emits a message only when `items_available > 0` **and** no notification was recorded for that store today (UTC). `DatabaseHandler.get_items()` uses `scan` with a filter, not `query` — full-table scan by design given the tiny table.

### Vendored TGTG client

`app/services/tgtg_service/tgtg_client.py` is a **vendored fork** of the `tgtg` PyPI package (the dependency was deliberately removed — see commits `7600589` / `1bb5131`). It owns login-by-email polling, token refresh, and the `datadome` cookie. When TGTG changes its API (endpoint versions like `item/v8/`, `auth/v5/`, or headers), patch this file — there is no upstream to bump.

### Layers

`app/core/` — infrastructure-facing (`scheduler.py` = EventBridge/Lambda, `database_handler.py` = DynamoDB, `telegram_bot_handler.py` = python-telegram-bot wiring).
`app/services/` — domain (`tgtg_service_monitor.py` orchestrates a monitoring run, `tgtg_service/` holds the client, pydantic `models.py` for the TGTG payload, and `notification_formatter.py`).
`app/common/` — `utils.py` (env vars, Telegram send, localization, Lambda env mutation), `constants.py`, `localizable.json`.

### Telegram bot

`TelegramBotHandler` runs python-telegram-bot in **webhook mode inside a Lambda invocation**: `initialize()` → `process_update()` → `shutdown()` per request, with no persistent application state. Any per-user state must go to DynamoDB or env vars.

All user-facing strings live in `app/common/localizable.json` under `en` / `fr` — add a key to **both** locales; `Utils.localize()` returns `""` (silently blank message) on a missing key. Every command has a paired inline button; adding a command means touching `_register_handlers`, `_callback_query_handler`, `_set_bot_commands`, and both locale blocks.

## Conventions

- Multi-line signature style (one parameter per line) is used across the codebase — match it.
- Logging via `app.common.logger.LOGGER` only. Existing logs print raw tokens/credentials at INFO; don't add new ones.
- Errors: custom exceptions in `app/core/exceptions.py` (DB) and `app/services/tgtg_service/exceptions.py` (TGTG). `_monitor_favorites` is the single place that maps exception type → user-facing Telegram message + cooldown decision.
- Tests use `unittest.TestCase` classes with `unittest.mock`, `freezegun` for time, and shared fixtures in `tests/conftest.py`. Test files mirror the module they cover; boto3 clients are always mocked (no moto).
