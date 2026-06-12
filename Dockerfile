FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py comments_common.py router.py tiktok_parser.py youtube_parser.py vk_parser.py ./

CMD ["python", "bot.py"]
