import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# httpx logs every request URL at INFO — and every Telegram API URL carries the
# bot token in its path, so INFO here means the token in plain text in every
# log line, container logs included.
logging.getLogger("httpx").setLevel(logging.WARNING)
