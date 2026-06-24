FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Контекст сборки — сама папка backend/ (самодостаточна): requirements.txt и код
# лежат здесь же. Это позволяет хостингу указать Root Directory = backend и не
# тащить в контекст фронт/telegram (как у frontend/ и telegram/ — каждый со своим).
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

# Shell-форма CMD, чтобы развернуть $PORT: PaaS (Railway/Render) задают порт через
# переменную окружения и маршрутизируют на него. Локально docker-compose
# переопределяет command (порт 8000), поэтому фолбэк сохраняет dev-поведение.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
