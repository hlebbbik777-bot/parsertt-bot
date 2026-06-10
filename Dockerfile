FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py tiktok_parser.py ./

CMD ["python", "bot.py"]
