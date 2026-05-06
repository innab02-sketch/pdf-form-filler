# PDF Form Filler Telegram Bot

A Telegram bot that automatically fills flat PDF forms with personal details using AI vision. It handles Hebrew (RTL) text properly and is designed to work with non-fillable PDFs (where fields are just blank lines).

## Features

- Receives PDF files via Telegram
- Asks which profile to use (Inna or Arik)
- Uses OpenAI Vision API to detect blank fields on each page
- Skips payment/credit card fields automatically
- Overlays Hebrew text correctly (RTL) using ReportLab
- Returns the filled PDF back to the user

## Prerequisites

- Python 3.9+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- OpenAI API Key (with access to `gpt-4o-mini` or `gpt-4.1-mini`)

## Local Setup

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set environment variables:
   ```bash
   export TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
   export OPENAI_API_KEY="your_openai_api_key"
   ```
4. Run the bot:
   ```bash
   python bot.py
   ```

## Deployment to Railway (Recommended)

Railway is a great platform for hosting Telegram bots because it's easy to set up and has a generous free tier.

1. Create a GitHub repository and push these files to it.
2. Go to [Railway.app](https://railway.app/) and sign in with GitHub.
3. Click **New Project** -> **Deploy from GitHub repo**.
4. Select your repository.
5. Once the project is created, go to the **Variables** tab.
6. Add the following environment variables:
   - `TELEGRAM_BOT_TOKEN`: Your bot token from BotFather
   - `OPENAI_API_KEY`: Your OpenAI API key
7. Railway will automatically detect the `requirements.txt` and `Procfile` and deploy your bot.

## Deployment to Render

1. Create a GitHub repository and push these files to it.
2. Go to [Render.com](https://render.com/) and sign in.
3. Click **New** -> **Background Worker**.
4. Connect your GitHub repository.
5. Set the following:
   - **Environment**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
6. Add the environment variables (`TELEGRAM_BOT_TOKEN` and `OPENAI_API_KEY`).
7. Click **Create Background Worker**.

## Customizing Profiles

You can edit the `profiles.json` file to add, remove, or modify the personal details that the bot uses to fill the forms.

## How it Works

Since the PDFs are "flat" (they don't have interactive form fields), the bot uses a clever approach:
1. It converts each PDF page to an image.
2. It sends the image to OpenAI's Vision API, asking it to find blank lines and return their coordinates.
3. It uses ReportLab to create a transparent PDF overlay with the text at those exact coordinates.
4. It merges the overlay with the original PDF page.
5. It uses `arabic_reshaper` and `python-bidi` to ensure Hebrew text is rendered correctly from right to left.
