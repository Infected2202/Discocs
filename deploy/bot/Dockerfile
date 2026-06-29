FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY discocs_bot/pyproject.toml discocs_bot/README.md ./
COPY discocs_bot/bot ./bot

RUN python -m pip install --upgrade pip \
    && python -m pip install -e .

CMD ["discocs-bot"]
