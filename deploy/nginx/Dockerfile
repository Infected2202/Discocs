FROM node:24-alpine AS ui-builder
WORKDIR /build
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN npm install -g pnpm@11 && pnpm install --frozen-lockfile
COPY ui/ ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:alpine
# Template is processed by nginx entrypoint via envsubst on startup
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=ui-builder /build/dist /usr/share/nginx/html
EXPOSE 8080
