# TooGoodToMiss 

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)


<p align="center">
  <img src="https://github.com/jordantete/TooGoodToMiss/assets/2340374/f62f2f94-957d-4279-8c77-3214b687299b" alt="MarineGEO circle logo" style="height: 150px; width:150px;"/>
</p>


## 📌 Overview

**TooGoodToMiss** is a customizable notification bot for TooGoodToGo (TGTG) deals, designed to monitor TGTG magic bags and notify users via Telegram. The bot runs as a single long-lived process (`python -m app.main`) and is designed to be deployed on any VPS you control via SSH.

## 🚀 Features

- 🔄 **Automated Monitoring:** Tracks TGTG magic bags on a randomized schedule and sends timely notifications.
- 💬 **Telegram Integration:** Allows users to interact with the bot through Telegram commands.
- 🌍 **Multi-language Support:** Available in English and French.
- 🖥️ **VPS-friendly:** Single process, no cloud provider lock-in — runs anywhere Python 3.10+ runs.
- 🛠️ **Modular Architecture:** Easy to extend and adapt to new use cases.

## 🧑‍💻 Setup Instructions

### 🖥️ Prerequisites

1. **A VPS (or any always-on machine) with Python 3.10+ and SSH access.**
2. **Telegram Bot Token & Chat ID**
    - **Telegram Bot Token**: This bot requires a Telegram Bot Token to communicate with Telegram's API.
    - **Chat ID**: The bot needs the `chat_id` of the user or group to send notifications.

## Installation

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env   # puis renseigner les valeurs
```

## Lancer en local

```bash
./.venv/bin/python -m app.main
```

## Déployer sur un VPS

Renseigner `VPS_USER`, `VPS_HOST`, `VPS_BOT_PATH` et `SSH_KEY` dans `.env`, puis :

```bash
./scripts/deploy.sh
```

Le script synchronise le code, pousse le `.env`, installe les dépendances dans un venv distant et redémarre la session tmux `toogoodtomiss`.

```bash
ssh $VPS_USER@$VPS_HOST 'tail -f /root/toogoodtomiss/logs/app.log'   # logs
ssh $VPS_USER@$VPS_HOST 'tmux attach -t toogoodtomiss'               # session
```

## Dépannage

### Le bot utilise encore d'anciens tokens TGTG après un déploiement

C'est le comportement attendu. Les variables `ACCESS_TOKEN`, `REFRESH_TOKEN`, `TGTG_COOKIE` et `USER_LANGUAGE` du `.env` ne servent qu'**une fois**, à la création de `state.json`. Ensuite `state.json` fait autorité, et `deploy.sh` ne le pousse jamais — sinon chaque déploiement écraserait la session TGTG vivante par une copie locale périmée, ce qui déclencherait un re-login et un CAPTCHA.

Pour forcer de nouveaux tokens :

```bash
ssh $VPS_USER@$VPS_HOST "rm /root/toogoodtomiss/state.json"
./scripts/deploy.sh
```

### Le monitoring ne se déclenche jamais

Vérifier que l'extra `[job-queue]` est bien installé : sans APScheduler, `application.job_queue` vaut `None`. `app.main.build_application()` lève une `RuntimeError` explicite dans ce cas.

### 🛠️ Telegram Setup

To interact with the bot, you need to create a **Telegram Bot** and get its **bot_token** and your **chat_id**:

1. **Create a Telegram Bot**
    - Open the Telegram app and search for the **BotFather**.
    - Start a chat with the BotFather and use the command `/newbot`.
    - Follow the instructions to create your bot and get the **Bot Token**.

2. **Get Your Chat ID**
    - Send a message to your bot to start a conversation.
    - Use the following URL to find your chat ID:
      ```plaintext
      https://api.telegram.org/bot<YourBotToken>/getUpdates
      ```
    - This will return a JSON response containing your chat ID.

## 🤝 Contributing

Contributions are welcome! If you have ideas, improvements, or bug fixes, feel free to submit an issue or a pull request. Please ensure that your contributions follow the project’s coding standards and include clear descriptions for any changes.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](./LICENSE.txt) file for more details.

## 👀 Feedback

Have any questions or feedback? Feel free to reach out via the issues tab on GitHub. We’d love to hear from you!

## ❗ Disclaimer

**TooGoodToMiss** is an independent project and is not affiliated with, endorsed by, or officially connected to TooGoodToGo (TGTG) or any of its subsidiaries or affiliates. All product names, logos, and brands are property of their respective owners.
