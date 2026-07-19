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
#
# Зависимости ставятся ДО `COPY app`, отдельным слоем с --no-install-project:
# essentia-tensorflow и umap тяжёлые, а `app/` меняется почти в каждом коммите —
# при обратном порядке (как было) любая правка кода переустанавливала весь
# зависимый стек заново. Теперь слой инвалидируется только по uv.lock.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra essentia

# umap-learn питает построение 2D-проекции коллекции (embedding-atlas / карта,
# см. docs/collection-map.md). Его нет в uv.lock (backend синкается --frozen),
# а импортируется он лениво — поэтому доставляем extra `map` отдельным слоем
# поверх frozen-venv. numpy держим <2, чтобы транзитивные зависимости umap не
# затащили numpy 2.x и не сломали essentia-tensorflow.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python "umap-learn>=0.5" "numpy<2"

# Финальный sync доставляет в venv сам проект — единственный шаг, который
# обязан видеть исходники. --inexact критичен: обычный `uv sync` приводит
# окружение в точное соответствие lock-файлу и снёс бы umap/numpy, которых
# в lock нет.
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --inexact --extra essentia

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 7752

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7752"]
