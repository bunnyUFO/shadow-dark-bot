FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data \
    && useradd --create-home --uid 1000 bot \
    && chown -R bot:bot /app
USER bot

CMD ["python", "-m", "shadowdark_bot.main"]
