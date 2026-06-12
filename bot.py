"""Telegram bot for parsing social media comments."""

from __future__ import annotations

import asyncio
import fcntl
import io
import logging
import os
import re
import sys
import threading

from flask import Flask
from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from comments_common import ParseError, to_csv_bytes
from router import detect_url, fetch_comments

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_RE = re.compile(
    r"https?://(?:www\.)?(?:"
    r"(?:vm|vt|m)\.tiktok\.com/\S+|tiktok\.com/\S+|"
    r"youtube\.com/\S+|youtu\.be/\S+|"
    r"(?:vk|m\.vk)\.(?:com|ru)/(?:video|wall|clip|clips)\S+"
    r")",
    re.I,
)
INSTAGRAM_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[^\s/]+",
    re.I,
)
LOCK_PATH = "/tmp/parsertt-bot.lock"

PLATFORM_NAMES = {
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "vk": "VK",
}


def acquire_single_instance_lock():
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error("Another bot instance is already running")
        sys.exit(1)
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


async def log_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        user = update.effective_user
        logger.info(
            "Incoming message from %s (%s): %r",
            user.username if user else "?",
            user.id if user else "?",
            update.message.text,
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning("Telegram network issue: %s", context.error)
        return
    logger.exception("Handler error for update %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("❌ Произошла ошибка. Попробуй ещё раз.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я парсер комментариев из соцсетей.\n\n"
        "Отправь ссылку на пост/видео:\n"
        "• TikTok\n"
        "• YouTube\n"
        "• VK\n\n"
        "Пришлю CSV-таблицу с комментариями.",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n"
        "/start — начать\n"
        "/help — помощь\n\n"
        "Поддерживаемые ссылки:\n"
        "TikTok — vt.tiktok.com, tiktok.com/.../video/...\n"
        "YouTube — youtube.com/watch, youtu.be, shorts\n"
        "VK — vk.com/wall..., vk.ru/wall...\n\n"
        "Результат — CSV для Excel/Numbers.",
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if INSTAGRAM_URL_RE.search(text):
        await update.message.reply_text(
            "Instagram временно отключён.\n"
            "Сейчас работают: TikTok, YouTube, VK."
        )
        return

    match = URL_RE.search(text)
    if not match:
        await update.message.reply_text(
            "Отправь ссылку на TikTok, YouTube или VK."
        )
        return

    url = match.group(0)
    detected = detect_url(url)
    platform = detected[0] if detected else "unknown"
    platform_name = PLATFORM_NAMES.get(platform, platform)

    status = await update.message.reply_text(f"⏳ Определяю {platform_name}...")

    try:
        await status.edit_text(f"⏳ Парсю комментарии {platform_name}...\nЭто может занять 1–3 мин.")
        data = await asyncio.to_thread(fetch_comments, url)
    except ParseError as exc:
        try:
            await status.edit_text(f"❌ {exc}")
        except Exception:
            await update.message.reply_text(f"❌ {exc}")
        return
    except Exception as exc:
        logger.exception("Parse failed for %s", url)
        try:
            await status.edit_text(f"❌ Ошибка при парсинге: {exc}")
        except Exception:
            await update.message.reply_text(f"❌ Ошибка при парсинге: {exc}")
        return

    csv_bytes = to_csv_bytes(data["comments"])
    summary = (
        f"✅ Готово — {platform_name}\n\n"
        f"📊 На платформе: {data.get('reported_total', '?')} коммент.\n"
        f"📥 Собрано: {data['total_comments']} "
        f"({data['top_level_comments']} корневых + {data['replies']} ответов)"
    )
    try:
        await status.edit_text(summary)
    except Exception:
        pass

    filename = f"{platform}_{data['source_id']}.csv"
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename=filename,
        caption=f"CSV — комментарии {platform_name}",
    )


def build_application() -> Application:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=180.0,
        write_timeout=180.0,
        pool_timeout=60.0,
    )
    app = (
        Application.builder()
        .token(token)
        .request(request)
        .get_updates_request(request)
        .build()
    )
    app.add_error_handler(on_error)
    app.add_handler(MessageHandler(filters.ALL, log_incoming), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    return app


def run_polling(app: Application) -> None:
    logger.info("Starting polling mode...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(
        drop_pending_updates=False,
        close_loop=False,
        bootstrap_retries=-1,
        poll_interval=1.0,
    )


def run_with_health_server(app: Application) -> None:
    port = int(os.environ.get("PORT", "10000"))
    flask_app = Flask(__name__)

    @flask_app.get("/")
    @flask_app.get("/health")
    def health():
        return "ok", 200

    thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0",
            port=port,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
    )
    thread.start()
    logger.info("Health server in background on port %s", port)
    run_polling(app)


def main() -> None:
    acquire_single_instance_lock()
    app = build_application()
    mode = os.environ.get("BOT_MODE", "auto").lower()

    if mode == "polling" or not os.environ.get("PORT"):
        run_polling(app)
    else:
        run_with_health_server(app)


if __name__ == "__main__":
    main()
