#!/bin/sh

set -u

STACK_DIR=/home/baba/threehands-website
PUBLIC_HEALTH_URL=https://threehands-website.tail220731.ts.net/healthz
LOCK_FILE=/run/lock/threehands-website-watchdog.lock
PROBE_COUNT=5
REQUIRED_SUCCESSES=4

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

probe_once() {
    response=$(curl --fail --silent --show-error --max-time 8 "$PUBLIC_HEALTH_URL" 2>/dev/null) || return 1
    [ "$response" = "ok" ]
}

probe_series() {
    successes=0
    attempt=1

    while [ "$attempt" -le "$PROBE_COUNT" ]; do
        if probe_once; then
            successes=$((successes + 1))
        fi

        if [ "$attempt" -lt "$PROBE_COUNT" ]; then
            sleep 2
        fi
        attempt=$((attempt + 1))
    done

    log "public Funnel probes: ${successes}/${PROBE_COUNT} successful"
    [ "$successes" -ge "$REQUIRED_SUCCESSES" ]
}

wait_for_health() {
    container=$1
    attempts=$2
    attempt=1

    while [ "$attempt" -le "$attempts" ]; do
        status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
        if [ "$status" = "healthy" ]; then
            return 0
        fi
        sleep 2
        attempt=$((attempt + 1))
    done

    return 1
}

exec 9>"$LOCK_FILE"
if ! flock --nonblock 9; then
    log "another watchdog run is already active"
    exit 0
fi

if probe_series; then
    log "public Funnel is healthy"
    exit 0
fi

log "public Funnel is degraded; beginning ordered recovery"

if ! cd "$STACK_DIR"; then
    log "recovery failed: stack directory is unavailable"
    exit 1
fi

if ! docker compose restart tailscale; then
    log "recovery failed: could not restart Tailscale"
    exit 1
fi

if ! wait_for_health threehands-tailscale 45; then
    log "recovery failed: Tailscale did not become healthy within 90 seconds"
    exit 1
fi

if ! docker compose up -d --force-recreate website; then
    log "recovery failed: could not recreate Nginx"
    exit 1
fi

if ! wait_for_health threehands-website 30; then
    log "recovery failed: Nginx did not become healthy within 60 seconds"
    exit 1
fi

log "local services recovered; allowing Funnel registration to settle"
sleep 30

if probe_series; then
    log "ordered recovery succeeded"
    exit 0
fi

log "ordered recovery completed, but public Funnel remains degraded"
exit 1
