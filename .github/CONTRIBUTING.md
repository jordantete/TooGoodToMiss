# Contributing

Thanks for your interest in contributing to **TooGoodToMiss**!

## Getting Started

1. Fork the repository and clone your fork.
2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   ./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
   ```
3. Copy the environment template and fill it in:
   ```bash
   cp .env.example .env
   ```
4. Create a branch for your changes:
   ```bash
   git checkout -b my-feature
   ```

## Development Workflow

### Running the bot

```bash
./.venv/bin/python -m app.main
```

The bot runs as a single long-lived process: `run_polling()` handles Telegram commands, and monitoring runs as a `python-telegram-bot` job that reschedules itself.

### Running tests

```bash
# All tests
./.venv/bin/python -m pytest tests/ -q

# A single file
./.venv/bin/python -m pytest tests/test_scheduler.py -q

# With coverage
./.venv/bin/python -m pytest --cov=app --cov-report=term
```

Tests use `unittest.TestCase` classes with `unittest.mock`, and `freezegun` for anything time-dependent. There is no linter configured on this project — just match the style of the surrounding code (multi-line signatures, one parameter per line).

## Things that will bite you

This project has a few non-obvious invariants. Breaking any of them fails silently, so they are worth reading before you change the scheduling or state code.

### The monitoring loop is the only thing that reschedules itself

There is no cron, no external trigger, no safety net. `monitor_job` arms its own successor in a `finally` block, and `Scheduler.next_delay_seconds()` **never returns `None`** — outside the monitoring windows and on Sundays it returns a re-check delay instead. If a code path ever leaves a pass without scheduling the next one, monitoring stops for good until the process restarts, with no error.

Every job must be armed through `_arm_monitoring()`, which cancels pending jobs before arming a new one. Two concurrent chains would double the TGTG polling rate.

### The randomized delays are anti-fingerprinting, not comfort

10–20 minutes in the morning window, 2–5 minutes in the afternoon, nothing on Sundays. This exists to avoid TooGoodToGo's bot detection. Do not replace it with a fixed interval.

### Never log a token, refresh token or cookie

`logs/app.log` is persistent on the server. Log the event, never the secret. Third-party loggers (`httpx`, `httpcore`, `telegram`, `apscheduler`) are deliberately set to `WARNING` in `app/common/logger.py` because they print the Telegram API URL, which contains the bot token — don't raise them back to `INFO`.

### `state.json` and `.env` are never committed, and never deployed over

`state.json` holds the live TGTG session in plain text. It is git-ignored, and `scripts/deploy.sh` deliberately excludes it from the rsync: overwriting a live session with a stale local copy triggers a re-login and a CAPTCHA.

A consequence that surprises everyone once: the `.env` seed is **one-way**. `ACCESS_TOKEN`, `REFRESH_TOKEN`, `TGTG_COOKIE` and `USER_LANGUAGE` are only read when `state.json` is created. Editing them and redeploying has no effect until you delete `state.json` on the server.

### User-facing strings live in both locales

All user-facing text belongs in `app/common/localizable.json`, under both `en` and `fr`. A missing key silently returns an empty string, so the bot sends a blank message. Adding a command means touching `_register_handlers`, `_callback_query_handler`, `_set_bot_commands` and both locale blocks.

Code comments, docstrings and script output are written in English.

## Pull Requests

- Keep PRs focused on a single change.
- Add or update tests for any new behavior.
- Make sure the full test suite passes before opening a PR.
- Describe what you changed and why, and mention anything you could not verify.

## Reporting Issues

Open an issue describing the problem, including steps to reproduce, expected behavior, and your environment (OS, Python version). If it involves the TooGoodToGo API, please include the failing step — login, favorites fetch, or notification — and whether a CAPTCHA was involved. **Never paste tokens, cookies or the contents of your `.env` or `state.json`.**
