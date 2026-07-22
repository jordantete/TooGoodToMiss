<div align="center">

<img src="assets/logo.png" alt="TooGoodToMiss" width="140" height="140" />

# TooGoodToMiss

### Never miss a TooGoodToGo magic bag again

A self-hosted Telegram bot that watches your TooGoodToGo favourites and pings you the moment a bag drops.
<br>Single process, no cloud lock-in — deploy it on any VPS with one command.

<br>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/github/actions/workflow/status/jordantete/TooGoodToMiss/unit_tests.yml?style=flat-square&label=tests)](https://github.com/jordantete/TooGoodToMiss/actions/workflows/unit_tests.yml)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Telegram-bot-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://core.telegram.org/bots)

</div>

---

## Quick Start

```bash
# Clone & install
git clone https://github.com/jordantete/TooGoodToMiss.git && cd TooGoodToMiss
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Configure
cp .env.example .env   # then fill in USER_EMAIL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Run
./.venv/bin/python -m app.main
```

## How It Works

The bot runs as a single long-lived process. `run_polling()` handles your Telegram commands, while monitoring runs as a job that **reschedules itself** after every pass.

1. **Watch** — every few minutes, the bot fetches your TooGoodToGo favourites
2. **Notify** — when a bag becomes available, you get a Telegram message
3. **Deduplicate** — one notification per store per day, no spam
4. **Back off** — if TooGoodToGo throws a CAPTCHA, the bot pauses itself and retries later

Polling intervals are deliberately randomized — 10–20 minutes in the morning window, 2–5 minutes in the afternoon, nothing on Sundays. This is anti-fingerprinting, not a config knob.

## Features

- **Automated monitoring** — randomized schedule that stays under TooGoodToGo's radar
- **Telegram control** — `/status`, `/pause`, `/wakeup`, `/settings` and inline buttons
- **Multi-language** — English and French
- **Self-hosted** — one process, one file of state, no managed services
- **One-command deploy** — `./scripts/deploy.sh` syncs, installs and restarts over SSH
- **Crash-safe state** — atomic writes, so a power cut never leaves an unbootable bot

## Telegram Setup

You need a bot token and your chat ID:

1. **Create the bot** — open Telegram, talk to [@BotFather](https://t.me/BotFather), send `/newbot` and follow the prompts. You get a **bot token**.
2. **Find your chat ID** — send any message to your new bot, then open:
   ```
   https://api.telegram.org/bot<YourBotToken>/getUpdates
   ```
   The JSON response contains your `chat_id`.

Put both in `.env`, alongside the `USER_EMAIL` of your TooGoodToGo account.

## Deploying to a VPS

Fill in `VPS_USER`, `VPS_HOST`, `VPS_BOT_PATH` and `SSH_KEY` in `.env`, then:

```bash
./scripts/deploy.sh
```

The script rsyncs the code, pushes the `.env`, installs dependencies in a remote virtualenv and restarts the `toogoodtomiss` tmux session.

```bash
ssh $VPS_USER@$VPS_HOST 'tail -f /root/toogoodtomiss/logs/app.log'   # logs
ssh $VPS_USER@$VPS_HOST 'tmux attach -t toogoodtomiss'               # attach
```

## Running the Tests

```bash
./.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
```

## Troubleshooting

### The bot still uses old TooGoodToGo tokens after a deploy

This is expected. `ACCESS_TOKEN`, `REFRESH_TOKEN`, `TGTG_COOKIE` and `USER_LANGUAGE` are read from `.env` **only once**, when `state.json` is first created. After that `state.json` is the source of truth, and `deploy.sh` never pushes it — otherwise every deploy would overwrite the live session with a stale local copy, forcing a re-login and a CAPTCHA.

To force new tokens:

```bash
ssh $VPS_USER@$VPS_HOST "rm /root/toogoodtomiss/state.json"
./scripts/deploy.sh
```

### Monitoring never runs

Check that the `[job-queue]` extra is installed. Without APScheduler, `application.job_queue` is `None`; `app.main.build_application()` raises an explicit `RuntimeError` in that case.

## Contributing

Contributions are welcome. See the [contributing guide](.github/CONTRIBUTING.md) for the development workflow and the project invariants worth knowing before you touch the scheduler or the state layer. By participating you agree to the [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Support

If this bot saved you a few magic bags, you can [buy me a coffee](https://buymeacoffee.com/pownedj) ☕

## License

MIT — see [LICENSE.txt](./LICENSE.txt).

## Disclaimer

**TooGoodToMiss** is an independent project and is not affiliated with, endorsed by, or officially connected to TooGoodToGo (TGTG) or any of its subsidiaries or affiliates. All product names, logos, and brands are property of their respective owners.
