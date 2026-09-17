#!/bin/sh
# Entrypoint awg-сайдкара (см. deploy/prod/docker-compose.yml, сервис awg).
#
# Бот сидит в сетевом неймспейсе этого контейнера, поэтому всё, что тут падает,
# для бота выглядит как «интернета нет». Кроме подъёма туннеля скрипт следит за
# ним: при устаревшем handshake сначала пробует переприменить конфиг на месте
# (netns сохраняется, бот не трогается), и только если это не помогло — выходит
# с ошибкой, чтобы docker пересоздал контейнер целиком.
#
# Канонический источник — git (deploy/prod/start-awg.sh), на хост его кладёт
# стадия Deploy. Правки делать здесь, а не на сервере.

IF="awg0"
CONF="/etc/amnezia/amneziawg/awg0.conf"

# Как часто смотрим на туннель.
CHECK_INTERVAL="${AWG_CHECK_INTERVAL:-30}"
# Возраст handshake, после которого туннель считается мёртвым. Живой WG с
# keepalive обновляет его раз в ~2 минуты.
STALE_AFTER="${AWG_STALE_AFTER:-300}"
# Сколько раз пробуем вылечить на месте, прежде чем ронять контейнер.
SOFT_ATTEMPTS="${AWG_SOFT_ATTEMPTS:-2}"
# Счётчик жёстких перезапусков переживает `docker restart` (но не recreate) —
# по нему растёт пауза перед выходом, чтобы при недоступном сервере не молотить
# рестартами впустую.
RESTART_COUNTER="/tmp/awg-hard-restarts"

# --- sanity
if [ ! -f "$CONF" ]; then
  echo "Config not found: $CONF"
  ls -la /etc/amnezia/amneziawg || true
  exit 1
fi

# docker default gw/dev inside this netns
GW="$(ip route show default | awk 'NR==1{print $3}')"
DEV="$(ip route show default | awk 'NR==1{print $5}')"

# parse fields from config
ENDPOINT_IP="$(awk -F'=' '/^[[:space:]]*Endpoint[[:space:]]*=/ {gsub(/[[:space:]]/,"",$2); split($2,a,":"); print a[1]; exit}' "$CONF")"
ADDR="$(awk -F'=' '/^[[:space:]]*Address[[:space:]]*=/ {gsub(/[[:space:]]/,"",$2); print $2; exit}' "$CONF")"
MTU="$(awk -F'=' '/^[[:space:]]*MTU[[:space:]]*=/ {gsub(/[[:space:]]/,"",$2); print $2; exit}' "$CONF" || true)"

# strip wg-quick directives that break setconf
strip_conf() {
  awk '
    /^\[Interface\]/{sec="i"; print; next}
    /^\[Peer\]/{sec="p"; print; next}
    sec=="i" {
      key=$1; gsub(/[[:space:]]/,"",key)
      if (key ~ /^(Address|DNS|MTU|Table|PreUp|PostUp|PreDown|PostDown|SaveConfig)$/) next
      print; next
    }
    { print }
  ' "$CONF"
}

# Переприменить конфиг пира поверх живого интерфейса. Не пересоздаёт netns,
# поэтому безопасно для контейнеров, сидящих в нашей сети.
reload_peer() {
  TMP="/tmp/awg.conf.$$"
  strip_conf > "$TMP"
  awg setconf "$IF" "$TMP"
  rc=$?
  rm -f "$TMP"
  return $rc
}

routes_up() {
  # keep endpoint reachable OUTSIDE tunnel
  if [ -n "$ENDPOINT_IP" ] && [ -n "$GW" ] && [ -n "$DEV" ]; then
    ip route replace "${ENDPOINT_IP}/32" via "$GW" dev "$DEV" 2>/dev/null || true
  fi

  # keep LAN/private nets OUTSIDE VPN
  ip route replace 192.168.1.0/24 via "$GW" dev "$DEV" 2>/dev/null || true
  ip route replace 10.0.0.0/8     via "$GW" dev "$DEV" 2>/dev/null || true
  ip route replace 172.16.0.0/12  via "$GW" dev "$DEV" 2>/dev/null || true
  ip route replace 192.168.0.0/16 via "$GW" dev "$DEV" 2>/dev/null || true

  # default route through VPN
  ip route replace default dev "$IF"
}

handshake_age() {
  ts="$(awg show "$IF" latest-handshakes 2>/dev/null | cut -f2 | sort -n | tail -1)"
  [ -z "$ts" ] && ts=0
  if [ "$ts" -le 0 ]; then
    echo "-1"
    return
  fi
  echo "$(( $(date +%s) - ts ))"
}

# --- подъём интерфейса
routes_up
ip link del "$IF" 2>/dev/null || true
ip link add "$IF" type amneziawg
reload_peer || { echo "[FATAL] awg setconf failed"; exit 1; }

[ -n "$ADDR" ] && ip -4 address add "$ADDR" dev "$IF" 2>/dev/null || true
[ -n "$MTU" ] && ip link set mtu "$MTU" dev "$IF" 2>/dev/null || true
ip link set up dev "$IF"
routes_up

# block TA UI port from tunnel, allow only from LAN
if command -v iptables >/dev/null 2>&1; then
  CHAIN="LAN_ONLY_UI"

  iptables -N "$CHAIN" 2>/dev/null || true
  iptables -F "$CHAIN" 2>/dev/null || true

  iptables -C INPUT -j "$CHAIN" 2>/dev/null || iptables -I INPUT 1 -j "$CHAIN"

  iptables -A "$CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
  iptables -A "$CHAIN" -i lo -j ACCEPT
  iptables -A "$CHAIN" -i "$DEV" -s 192.168.1.0/24 -p tcp --dport 8532 -j ACCEPT
  [ -n "$GW" ] && iptables -A "$CHAIN" -i "$DEV" -s "$GW/32" -p tcp --dport 8532 -j ACCEPT
  iptables -A "$CHAIN" -p tcp --dport 8532 -j DROP
  iptables -A "$CHAIN" -j RETURN
else
  echo "WARN: iptables not found"
fi

echo "[OK] awg0 up"
ip a show "$IF" || true
ip r || true

# --- монитор: статистика + самолечение.
# Это основной процесс контейнера: его выход = перезапуск сайдкара docker'ом.
PREV_RX=0
PREV_TX=0
SOFT_TRIED=0

to_bytes() {
  val="$1"; unit="$2"
  case "$unit" in
    KiB) echo "$val" | awk '{printf "%d", $1*1024}' ;;
    MiB) echo "$val" | awk '{printf "%d", $1*1048576}' ;;
    GiB) echo "$val" | awk '{printf "%d", $1*1073741824}' ;;
    *)   echo "$val" | awk '{printf "%d", $1}' ;;
  esac
}

hard_restart() {
  count=0
  [ -f "$RESTART_COUNTER" ] && count="$(cat "$RESTART_COUNTER" 2>/dev/null || echo 0)"
  count=$(( count + 1 ))
  echo "$count" > "$RESTART_COUNTER" 2>/dev/null || true

  # 60с, 120с, 240с… но не больше 10 минут: когда лежит сам VPN-сервер (а не
  # туннель), перезапуски всё равно не помогают — контейнер в это время виден
  # как unhealthy, и это честнее, чем бесконечная карусель рестартов.
  delay=$(( 60 * count ))
  [ "$delay" -gt 600 ] && delay=600

  echo "[AWG-HEAL] soft-heal не помог; жёсткий перезапуск #$count через ${delay}s"
  sleep "$delay"
  exit 1
}

while true; do
  sleep "$CHECK_INTERVAL"

  TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  STATS="$(awg show "$IF" 2>&1)"
  HANDSHAKE="$(echo "$STATS" | awk '/latest handshake/{print $3,$4,$5}')"
  RX_RAW="$(echo "$STATS" | awk '/transfer/{print $2}')"
  TX_RAW="$(echo "$STATS" | awk '/transfer/{print $5}')"
  RX_UNIT="$(echo "$STATS" | awk '/transfer/{print $3}')"
  TX_UNIT="$(echo "$STATS" | awk '/transfer/{print $6}')"

  CUR_RX="$(to_bytes "$RX_RAW" "$RX_UNIT")"
  CUR_TX="$(to_bytes "$TX_RAW" "$TX_UNIT")"
  if [ "$PREV_RX" -gt 0 ] 2>/dev/null; then
    RX_SPEED=$(( (CUR_RX - PREV_RX) / CHECK_INTERVAL / 1024 ))
    TX_SPEED=$(( (CUR_TX - PREV_TX) / CHECK_INTERVAL / 1024 ))
    SPEED_STR="| dl: ${RX_SPEED} KB/s | ul: ${TX_SPEED} KB/s"
  else
    SPEED_STR=""
  fi
  PREV_RX="$CUR_RX"
  PREV_TX="$CUR_TX"

  echo "[AWG-MONITOR] $TS | handshake: $HANDSHAKE | rx: $RX_RAW $RX_UNIT | tx: $TX_RAW $TX_UNIT $SPEED_STR"

  AGE="$(handshake_age)"
  if [ "$AGE" -ge 0 ] && [ "$AGE" -lt "$STALE_AFTER" ]; then
    if [ "$SOFT_TRIED" -gt 0 ]; then
      echo "[AWG-HEAL] туннель ожил (handshake ${AGE}s назад) после $SOFT_TRIED попыток"
      SOFT_TRIED=0
      rm -f "$RESTART_COUNTER" 2>/dev/null || true
    fi
    continue
  fi

  SOFT_TRIED=$(( SOFT_TRIED + 1 ))
  if [ "$SOFT_TRIED" -gt "$SOFT_ATTEMPTS" ]; then
    hard_restart
  fi

  echo "[AWG-HEAL] handshake устарел (${AGE}s) — переприменяю конфиг, попытка $SOFT_TRIED/$SOFT_ATTEMPTS"
  reload_peer || echo "[AWG-HEAL] setconf не прошёл"
  ip link set up dev "$IF" 2>/dev/null || true
  routes_up
done
