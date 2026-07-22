# TooGoodToMiss 

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)


<p align="center">
  <img src="https://github.com/jordantete/TooGoodToMiss/assets/2340374/f62f2f94-957d-4279-8c77-3214b687299b" alt="MarineGEO circle logo" style="height: 150px; width:150px;"/>
</p>


## 📌 Overview

**TooGoodToMiss** is a customizable notification bot for TooGoodToGo (TGTG) deals, designed to monitor TGTG magic bags and notify users via Telegram. The bot is built with a modular architecture and is optimized for efficient event-driven operations, making it easy to deploy in a serverless environment on AWS Lambda or any other cloud provider (GCP, Azure, etc.). It is ready to be customized and adapted to fit your needs.

## 🚀 Features

- 🔄 **Automated Monitoring:** Tracks TGTG magic bags on a set schedule and sends timely notifications.
- 💬 **Telegram Integration:** Allows users to interact with the bot through Telegram commands.
- 🌍 **Multi-language Support:** Available in English and French.
- ☁️ **Cloud Compatibility:** Optimized for AWS Lambda, but easily customizable for other cloud providers like GCP and Azure.
- 🛠️ **Modular Architecture:** Easy to extend and adapt to new use cases.

## 🧑‍💻 Setup Instructions

### 🖥️ Prerequisites

1. **Cloud Account (AWS, GCP, Azure, etc.)**
    - **AWS**: This bot is primarily designed for AWS Lambda. You will need an AWS account to deploy the bot, but it can also be customized for other cloud platforms like GCP or Azure. If you are using AWS, you’ll need to configure your AWS credentials and region.
    - **Other Cloud Providers**: If you're using GCP or Azure, you'll need to update the `serverless.yml` configuration to match the settings for your chosen provider (e.g., `gcp` or `azure`).

2. **Telegram Bot Token & Chat ID**
    - **Telegram Bot Token**: This bot requires a Telegram Bot Token to communicate with Telegram's API.
    - **Chat ID**: The bot needs the `chat_id` of the user or group to send notifications.

### 🚀 Installation

1. **Clone the Repository**:
  ```sh
  git clone https://github.com/jordantete/TooGoodToMiss.git
  cd TooGoodToMiss
  ```

2. **Set Up Conda Environment**:
  ```sh
  conda env create -f environment.yml
  conda activate TooGoodToMiss
  ```

3. **Configure Environment Variables in Conda**:

To set multiple environment variables at once, use a `.env` file. This is faster and keeps your configuration organized.

At the root of the project, create a `.env` file and add the necessary environment variables:

  ```plaintext
  # .env
  USER_EMAIL=your_user_email@example.com
  TELEGRAM_BOT_TOKEN=your_telegram_bot_token
  TELEGRAM_CHAT_ID=your_telegram_chat_id
  AWS_ACCOUNT_ID=your_aws_user_account_id
  DEFAULT_AWS_REGION=your_aws_region
  ```

4. **Creating the Lambda Layer**:

To create the Lambda layer, use the following commands:

  ```sh
  cd lambda_layer
  mkdir -p python
  pip install \
      --platform manylinux2014_x86_64 \
      --target=python \
      --implementation cp \
      --python-version 3.10 \
      --only-binary=:all: \
      -r requirements_layer.txt
  zip -r lambda_layer.zip python/
  aws lambda publish-layer-version --layer-name TooGoodToMissLayer --description "Layer for dependencies" --zip-file fileb://lambda_layer.zip --compatible-runtimes python3.10
  ```

### ☁️ Cloud Deployment

#### AWS Setup
- You must have an **AWS account** to deploy your bot. This bot uses AWS Lambda for serverless deployment, but it can be easily adapted to other cloud providers like GCP or Azure by updating the `serverless.yml` configuration file.

To deploy to AWS:

1. **Configure the AWS CLI**
    ```sh
    aws configure
    ```

2. **Deploy with Serverless Framework**
    - Install the Serverless framework if you haven’t already:
      ```sh
      npm install -g serverless
      ```

    - Deploy the bot to AWS Lambda using the Serverless framework:
      ```sh
      serverless deploy --stage dev
      ```

    **Customizing for GCP or Azure:** You can easily adapt this project for GCP or Azure by modifying the `serverless.yml` file. Serverless makes it easy to switch providers—just update the `provider` configuration.

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