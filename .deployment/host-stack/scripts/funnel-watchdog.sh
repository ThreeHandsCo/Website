#!/bin/sh

# Funnel watchdog for the Three Hands website origin.
#
# Probes the PUBLIC funnel path (Tailscale ingress relays) and self-heals the
# two known failure modes:
#   1. Packet-filter desync: the node drops ingress relay traffic with
#      "no rules matched". Fixed by `tailscale up` (re-applies the netmap).
#   2. Orphaned website netns: recreating the tailscale container leaves the
#      nginx container in a dead network namespace. Fixed by force-recreating
#      the website service.
#
# The probe resolves the public relay IPs via public DNS and pins each probe
# to one relay (--resolve), so a relay that still works cannot mask a relay
# that is being dropped. ALL relays must pass.

set -u

STACK_DIR=/home/baba/threehands-website
PUBLIC_HOST=threehands-website.tail220731.ts.net
PUBLIC_HEALTH_URL="https://${PUBLIC_HOST}/healthz"
DNS_RESOLVER=1.1.1.1
LOCK_FILE=/run/lock/threehands-website-watchdog.lock
PROBE_COUNT=4
REQUIRED_SUCCESSES=3

log() {
    printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

relay_ips() {
    # Public funnel relay IPs; fall back to the system answer.
    ips=$(dig +short A "$PUBLIC_HOST" @"$DNS_RESOLVER" 2>/dev/null | grep -E '^[0-9.]+$')
    if [ -z "$ips" ]; then
        ips=$(getent ahostsv4 "$PUBLIC_HOST" | awk '{print $1}' | sort -u)
    fi
    printf '%s\n' "$ips"
}

probe_once() {
    relay=$1
    response=$(curl --fail --silent --show-error --max-time 8 \
        --resolve "${PUBLIC_HOST}:443:${relay}" \
        "$PUBLIC_HEALTH_URL" 2>/dev/null) || return 1
    [ "$response" = "ok" ]
}

# Returns 0 only if EVERY relay reaches REQUIRED_SUCCESSES of PROBE_COUNT.
probe_all_relays() {
    all_ok=1
    any_relay=0

    for relay in $(relay_ips); do
        any_relay=1
        successes=0
        attempt=1
        while [ "$attempt" -le "$PROBE_COUNT" ]; do
            if probe_once "$relay"; then
                successes=$((successes + 1))
            fi
            if [ "$attempt" -lt "$PROBE_COUNT" ]; then
                sleep 2
            fi
            attempt=$((attempt + 1))
        done
        log "relay ${relay}: ${successes}/${PROBE_COUNT} probes ok"
        if [ "$successes" -lt "$REQUIRED_SUCCESSES" ]; then
            all_ok=0
        fi
    done

    [ "$any_relay" -eq 1 ] && [ "$all_ok" -eq 1 ]
}

netns_shared() {
    ns_ts=$(docker exec threehands-tailscale sh -c 'readlink /proc/self/ns/net' 2>/dev/null || true)
    ns_web=$(docker exec threehands-website sh -c 'readlink /proc/self/ns/net' 2>/dev/null || true)
    [ -n "$ns_ts" ] && [ "$ns_ts" = "$ns_web" ]
}

container_egress_ok() {
    docker run --rm alpine:3 sh -c "wget -q -O- -T 6 http://1.1.1.1 >/dev/null 2>&1"
}

host_egress_ok() {
    curl --fail --silent --max-time 6 https://www.google.com/generate_204 >/dev/null 2>&1
}

# Last-resort escalation for the recurring "bridge NAT silently rots" failure:
# container egress dead while the host network is fine. Nothing short of a
# daemon restart reprograms the plumbing (Sep 5, Sep 29). Restarts every
# container on the host; all of them carry restart policies.
restart_docker_daemon() {
    log "container egress dead while host network is fine; restarting docker daemon"
    docker run --rm --privileged --pid=host --net=host alpine:3 \
        sh -c "nsenter -t 1 -m -u -i -n -p systemctl restart docker" >/dev/null 2>&1
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

if probe_all_relays; then
    log "public Funnel is healthy on all relays"
    exit 0
fi

log "public Funnel is degraded; beginning ordered recovery"

if ! cd "$STACK_DIR"; then
    log "recovery failed: stack directory is unavailable"
    exit 1
fi

# 0. If container egress itself is dead (host network fine), only a docker
#    daemon restart reprograms the bridge NAT. Do this BEFORE touching
#    tailscale, or every later step runs without a network.
if ! container_egress_ok && host_egress_ok; then
    restart_docker_daemon
    sleep 15
    if ! container_egress_ok; then
        log "recovery failed: container egress still dead after daemon restart"
        exit 1
    fi
    log "container egress restored by daemon restart; waiting for stack to settle"
    wait_for_health threehands-tailscale 60 || true
    wait_for_health threehands-website 30 || true
fi

# 1. Re-apply the netmap: fixes packet-filter desync (drops of ingress relays).
if ! docker exec threehands-tailscale tailscale up --accept-dns=false >/dev/null 2>&1; then
    log "recovery step 1 failed: tailscale up did not succeed; trying container restart"
    docker compose restart tailscale >/dev/null 2>&1 || true
fi

if ! wait_for_health threehands-tailscale 45; then
    log "recovery failed: Tailscale did not become healthy within 90 seconds"
    exit 1
fi

# 2. Ensure the website container still shares the tailscale netns.
if ! netns_shared; then
    log "website netns diverged; recreating website container"
    if ! docker compose up -d --force-recreate website; then
        log "recovery failed: could not recreate Nginx"
        exit 1
    fi
fi

if ! wait_for_health threehands-website 30; then
    log "recovery failed: Nginx did not become healthy within 60 seconds"
    exit 1
fi

log "local services recovered; allowing Funnel registration to settle"
sleep 30

if probe_all_relays; then
    log "ordered recovery succeeded"
    exit 0
fi

log "ordered recovery completed, but public Funnel remains degraded"
exit 1
