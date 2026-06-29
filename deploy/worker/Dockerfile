# syntax=docker/dockerfile:1.7
ARG BASE_IMAGE=tensorflow/tensorflow:2.15.0-gpu
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TF_CPP_MIN_LOG_LEVEL=3
ENV DISCOCS_DATA_DIR=/worker-data
ENV DISCOCS_DB_PATH=/worker-data/worker-local.db
ENV DISCOCS_MODEL_DIR=/app/models
ENV DISCOCS_INDEX_DIR=/worker-data
ENV DISCOCS_AUDIO_LOADER=ffmpeg
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV CUDA_VISIBLE_DEVICES=0
ENV DISCOCS_WORKER_SERVER=http://192.168.1.41:8711
ENV DISCOCS_WORKER_ID=gpu-worker-1
ENV DISCOCS_WORKER_EMBEDDING_MODEL=discogs_multi
ENV DISCOCS_WORKER_AUDIO_FEATURE_MODEL=audio_features_v1
ENV DISCOCS_WORKER_HEAD_MODEL=discogs-effnet-heads
ENV DISCOCS_WORKER_MUQ_MODEL=muq_mulan
ENV DISCOCS_WORKER_CLAIM_BATCH_SIZE=2
ENV DISCOCS_WORKER_MAX_INFLIGHT_TASKS=2
ENV DISCOCS_WORKER_DOWNLOAD_CONCURRENCY=1
ENV DISCOCS_WORKER_SUBMIT_BATCH_SIZE=1
ENV DISCOCS_WORKER_LEASE_SECONDS=900
ENV DISCOCS_WORKER_POLL_SECONDS=5
ENV DISCOCS_WORKER_CPU_WORKERS=0
ENV DISCOCS_WORKER_GPU_BATCH_SIZE=64
ENV DISCOCS_WORKER_READY_BATCHES=1
ENV DISCOCS_WORKER_EMBEDDING_BACKEND=auto
ENV DISCOCS_EFFNET_BACKEND=auto
ENV DISCOCS_FFMPEG_THREADS=1
ENV DISCOCS_MUQ_MODEL_NAME=OpenMuQ/MuQ-MuLan-large
ENV DISCOCS_MUQ_CACHE_DIR=/app/models/muq
ENV DISCOCS_MUQ_DEVICE=cuda
ENV OMP_NUM_THREADS=1
ENV TORCHDYNAMO_DISABLE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./

RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p app \
    && touch app/__init__.py \
    && python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[essentia,muq]" \
    && rm -rf app

COPY app ./app

RUN python -m pip install -e . --no-deps \
    && mkdir -p /worker-data /app/models

CMD ["sh", "-c", "exec python -m app.cli worker --server \"$DISCOCS_WORKER_SERVER\" --worker-id \"$DISCOCS_WORKER_ID\" --models \"$DISCOCS_WORKER_EMBEDDING_MODEL\" --models \"$DISCOCS_WORKER_AUDIO_FEATURE_MODEL\" --models \"$DISCOCS_WORKER_HEAD_MODEL\" --models \"$DISCOCS_WORKER_MUQ_MODEL\" --claim-batch-size \"$DISCOCS_WORKER_CLAIM_BATCH_SIZE\" --max-inflight-tasks \"$DISCOCS_WORKER_MAX_INFLIGHT_TASKS\" --download-concurrency \"$DISCOCS_WORKER_DOWNLOAD_CONCURRENCY\" --submit-batch-size \"$DISCOCS_WORKER_SUBMIT_BATCH_SIZE\" --lease-seconds \"$DISCOCS_WORKER_LEASE_SECONDS\" --poll-seconds \"$DISCOCS_WORKER_POLL_SECONDS\" --cpu-workers \"$DISCOCS_WORKER_CPU_WORKERS\" --gpu-batch-size \"$DISCOCS_WORKER_GPU_BATCH_SIZE\" --ready-batches \"$DISCOCS_WORKER_READY_BATCHES\" --embedding-backend \"$DISCOCS_WORKER_EMBEDDING_BACKEND\""]
