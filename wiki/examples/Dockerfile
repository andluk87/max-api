FROM python:3.10-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY client.py bot.py ./
CMD ["python", "-u", "bot.py"]
