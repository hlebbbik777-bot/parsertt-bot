"""Telegram bot for parsing TikTok comments."""

from __future__ import annotations

import io
import logging
import os
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from tiktok_parser import TikTokParseError, fetch_all_comments, resolve_video_id, to_csv_bytes, to_json_bytes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://(?:www\.)?(?:vm|vt)\.tiktok\.com/\S+|https?://(?:www\.)?tiktok\.com/\S+", re.I)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Привет! Я *parser tik tok* — парсю комментарии с TikTok.\n\n"
        "Просто отправь ссылку на видео, например:\n"
        "`https://vt.tiktok.com/...`\n"
        "`https://www.tiktok.com/@user/video/...`\n\n"
        "Я пришлю JSON и CSV с комментариями.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Команды:*\n"
        "/start — начать\n"
        "/help — помощь\n\n"
        "*Как пользоваться:*\n"
        "Отправь ссылку на TikTok-видео — бот соберёт комментарии и пришлёт файлы.\n\n"
        "⚠️ Без авторизации TikTok отдаёт не все корневые комментарии, "
        "но все ответы к доступным — подтягиваются.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    match = URL_RE.search(text)
    if not match:
        await update.message.reply_text("Отправь ссылку на TikTok-видео.")
        return

    url = match.group(0)
    status = await update.message.reply_text("⏳ Определяю видео...")

    try:
        video_id, video_url = resolve_video_id(url)
    except TikTokParseError as exc:
        await status.edit_text(f"❌ {exc}")
        return

    await status.edit_text(f"⏳ Парсю комментарии...\nВидео: `{video_id}`", parse_mode=ParseMode.MARKDOWN)

    last_reported = {"page": 0}

    def progress_cb(*, page: int, collected: int, reported_total) -> None:
        last_reported["page"] = page
        last_reported["collected"] = collected
        last_reported["reported_total"] = reported_total

    try:
        data = fetch_all_comments(video_id, video_url, progress_cb=progress_cb)
    except Exception as exc:
        logger.exception("Parse failed for %s", url)
        await status.edit_text(f"❌ Ошибка при парсинге: {exc}")
        return

    json_bytes = to_json_bytes(data)
    csv_bytes = to_csv_bytes(data["comments"])

    summary = (
        f"✅ Готово!\n\n"
        f"📹 [Видео]({video_url})\n"
        f"📊 На TikTok: {data.get('reported_total', '?')} коммент.\n"
        f"📥 Собрано: {data['total_comments']} "
        f"({data['top_level_comments']} корневых + {data['replies']} ответов)"
    )
    await status.edit_text(summary, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

    base = f"tiktok_{video_id}"
    await update.message.reply_document(
        document=io.BytesIO(json_bytes),
        filename=f"{base}.json",
        caption="JSON — полные данные",
    )
    await update.message.reply_document(
        document=io.BytesIO(csv_bytes),
        filename=f"{base}.csv",
        caption="CSV — таблица для Excel",
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
