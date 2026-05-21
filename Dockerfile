FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# gosu lets the entrypoint drop privileges from root to the bot user
# after fixing data-dir ownership.
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src/ ./src/
COPY migrations/ ./migrations/
# Editable install so __file__ in shadowdark_bot.main resolves to /app/src/shadowdark_bot/...
# This lets run_migrations() find /app/alembic.ini via Path(__file__).parents[2].
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Container starts as root so the entrypoint can chown /app/data,
# then drops to the bot user via gosu before exec'ing the command.
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "shadowdark_bot.main"]
