FROM node:24-alpine AS ui-builder
WORKDIR /build
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN npm install -g pnpm@11 && pnpm install --frozen-lockfile
COPY ui/ ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:alpine
# Базовый nginx-unprivileged отстаёт от alpine-репозитория по системным
# пакетам (curl/libcurl и т.п.), и Trivy валит фиксабельные HIGH в них
# (напр. CVE-2026-5773 / CVE-2026-6276 в curl 8.19 -> фикс в 8.20). Обновляем
# пакеты поверх базы. SECURITY_REFRESH=GIT_SHA сбрасывает кэш этого слоя на
# каждом билде, чтобы подхватывать свежие фиксы — та же схема, что в
# backend/bot Dockerfile'ах (apt-get upgrade). apk требует root, поэтому
# временно поднимаемся до него и возвращаемся к штатному непривилегированному
# UID 101, под которым работает nginx-unprivileged.
ARG SECURITY_REFRESH=local
USER root
RUN echo "security refresh: ${SECURITY_REFRESH}" \
    && apk upgrade --no-cache
USER 101
# Template is processed by nginx entrypoint via envsubst on startup
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=ui-builder /build/dist /usr/share/nginx/html
# APK + OTA web bundle, populated by the separate Jenkinsfile.android job
# (see docs/android-app.md) into deploy/nginx/downloads/ before this image is
# built. Empty (just .gitkeep) on the regular per-push pipeline — the SPA
# fallback in default.conf.template already serves real files under this
# root before falling through to index.html, so no extra nginx location is needed.
COPY deploy/nginx/downloads /usr/share/nginx/html/downloads
EXPOSE 8080
