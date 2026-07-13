FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TF_CPP_MIN_LOG_LEVEL=3
ENV CUDA_VISIBLE_DEVICES=-1

WORKDIR /app

ARG SECURITY_REFRESH=local
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# --frozen — ставим строго из uv.lock, без пересчёта резолвера, чтобы прод
# получал ровно те версии, что закреплены в lock-файле (и что видит Trivy).
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra essentia

# umap-learn питает построение 2D-проекции коллекции (embedding-atlas / карта,
# см. docs/collection-map.md). Его нет в uv.lock (backend синкается --frozen),
# а импортируется он лениво — поэтому доставляем extra `map` отдельным слоем
# поверх frozen-venv. numpy держим <2, чтобы транзитивные зависимости umap не
# затащили numpy 2.x и не сломали essentia-tensorflow.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python "umap-learn>=0.5" "numpy<2"

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 7752

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7752"]
