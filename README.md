# parser tik tok — Telegram Bot

Telegram-бот [@parsertt_bot](https://t.me/parsertt_bot) для парсинга комментариев с TikTok.

## Использование

1. Открой бота: https://t.me/parsertt_bot
2. Отправь ссылку на TikTok-видео
3. Получи JSON и CSV с комментариями

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your_token"
python bot.py
```

## Деплой на Render (бесплатно)

1. Зайди на https://render.com и создай аккаунт
2. Нажми **New → Blueprint** и подключи репозиторий:
   https://github.com/hlebbbik777-bot/parsertt-bot
3. При деплое укажи переменную `TELEGRAM_BOT_TOKEN`
4. Render запустит worker с `python bot.py`

Или one-click: https://render.com/deploy?repo=https://github.com/hlebbbik777-bot/parsertt-bot

## Деплой на Fly.io

```bash
fly secrets set TELEGRAM_BOT_TOKEN="your_token"
fly deploy
```

> Fly.io требует привязку карты (даже для free tier).

## Структура

- `bot.py` — Telegram-бот
- `tiktok_parser.py` — парсер комментариев TikTok
- `render.yaml` — конфиг Render
- `Dockerfile` — Docker-образ
