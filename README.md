# Crypto Price Tracker Telegram Bot

A Telegram bot that tracks cryptocurrency token prices using the CoinGecko API.

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create a `.env` file in the project root with the following content:

```
TELEGRAM_TOKEN=your_telegram_bot_token_here
COINGECKO_API_KEY=your_coingecko_api_key_here
```

3. Get your tokens:
   - Telegram Bot Token: Message [@BotFather](https://t.me/botfather) on Telegram to create a new bot
   - CoinGecko API Key: Get from [CoinGecko Pro API](https://www.coingecko.com/en/api/pricing)

## Running the Bot

```bash
python bot.py
```

## Usage

1. Start the bot: `/start`
2. Track a token: `/track network=<network> address=<contract_address>`
   Example: `/track network=ethereum address=0x123...`
3. Stop tracking: `/stop`

## Features

- Track token prices in real-time
- Support for multiple networks
- One token tracking per user
- Price updates every minute
- Simple command interface
