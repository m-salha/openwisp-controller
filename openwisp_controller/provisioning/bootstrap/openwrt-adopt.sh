#!/bin/sh
# =============================================================================
# openwrt-adopt.sh — Lullex WiFi Provisioning bootstrap for OpenWrt
#
# USAGE
#   Install to /usr/sbin/lullex-adopt and call once at first boot via procd
#   or /etc/rc.local.  The script is idempotent: if /tmp/lullex-adopt.status
#   already contains "success" it exits immediately.
#
# DEPENDENCIES
#   curl, jsonfilter (busybox), uci, logger, wg (optional — for WireGuard)
#
# CONFIG  /etc/config/lullex-bootstrap  (UCI)
#   config adopt 'adopt'
#     option url    'https://controller.wifi.lullex.com/api/provision/adopt/'
#     option token  'REPLACE_WITH_ADOPTION_TOKEN'
#     option verify_ssl '1'    # set 0 only in lab/dev
# =============================================================================
set -e

BOOTSTRAP_CONFIG="/etc/config/lullex-bootstrap"
OPENWISP_CONFIG="/etc/config/openwisp"
WG_DIR="/etc/lullex"
STATUS_FILE="/tmp/lullex-adopt.status"
LOG_TAG="lullex-adopt"

log()  { logger -t "$LOG_TAG" "$*"; }
die()  { log "FATAL: $*"; echo "failed" > "$STATUS_FILE"; exit 1; }
info() { log "INFO:  $*"; }

# ── Guard: already done? ──────────────────────────────────────────────────────
if [ -f "$STATUS_FILE" ] && [ "$(cat "$STATUS_FILE")" = "success" ]; then
    info "Already adopted — exiting."
    exit 0
fi

# ── Read UCI bootstrap config ─────────────────────────────────────────────────
[ -f "$BOOTSTRAP_CONFIG" ] || die "Bootstrap config not found: $BOOTSTRAP_CONFIG"

PROVISION_URL=$(uci -q get lullex-bootstrap.adopt.url)   || die "Missing lullex-bootstrap.adopt.url"
TOKEN=$(uci -q get lullex-bootstrap.adopt.token)         || die "Missing lullex-bootstrap.adopt.token"
VERIFY_SSL=$(uci -q get lullex-bootstrap.adopt.verify_ssl 2>/dev/null || echo "1")

[ -n "$TOKEN" ] || die "Token is empty — set lullex-bootstrap.adopt.token"

# ── Gather device identity ────────────────────────────────────────────────────
# Prefer br-lan MAC; fall back to eth0 or the first ether interface found.
MAC=$(cat /sys/class/net/br-lan/address 2>/dev/null) \
  || MAC=$(cat /sys/class/net/eth0/address 2>/dev/null) \
  || MAC=$(ip link show | awk '/link\/ether/ {print $2; exit}') \
  || die "Cannot determine MAC address"

HOSTNAME=$(uci -q get system.@system[0].hostname 2>/dev/null || hostname)
MODEL=$(cat /tmp/sysinfo/model 2>/dev/null || echo "unknown")

# ── WireGuard keypair (generate once, persist) ────────────────────────────────
WG_PRIVKEY_FILE="$WG_DIR/wg-private.key"
WG_PUBKEY_FILE="$WG_DIR/wg-public.key"
WG_PUB=""

mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"

if command -v wg >/dev/null 2>&1; then
    if [ ! -f "$WG_PRIVKEY_FILE" ]; then
        wg genkey > "$WG_PRIVKEY_FILE"
        chmod 600 "$WG_PRIVKEY_FILE"
        wg pubkey < "$WG_PRIVKEY_FILE" > "$WG_PUBKEY_FILE"
        info "WireGuard keypair generated."
    fi
    WG_PUB=$(cat "$WG_PUBKEY_FILE")
else
    info "wg not found — WireGuard config will not be requested."
fi

# ── POST to provisioning endpoint ─────────────────────────────────────────────
info "Contacting $PROVISION_URL (mac=$MAC hostname=$HOSTNAME model=$MODEL)"

CURL_OPTS="-s --fail --max-time 30 -H 'Content-Type: application/json'"
[ "$VERIFY_SSL" = "0" ] && CURL_OPTS="$CURL_OPTS -k"

PAYLOAD=$(printf '{"token":"%s","mac_address":"%s","hostname":"%s","model":"%s","public_wireguard_key":"%s"}' \
    "$TOKEN" "$MAC" "$HOSTNAME" "$MODEL" "$WG_PUB")

RESPONSE=$(curl $CURL_OPTS -X POST -d "$PAYLOAD" "$PROVISION_URL") \
    || die "HTTP request to provisioning endpoint failed"

# ── Parse required fields ─────────────────────────────────────────────────────
CONTROLLER_URL=$(echo "$RESPONSE" | jsonfilter -e '@.controller_url' 2>/dev/null)
SHARED_SECRET=$(echo  "$RESPONSE" | jsonfilter -e '@.shared_secret'  2>/dev/null)
ORG_UUID=$(echo       "$RESPONSE" | jsonfilter -e '@.organization_uuid' 2>/dev/null)

[ -n "$CONTROLLER_URL" ] || die "No controller_url in provisioning response"
[ -n "$SHARED_SECRET"  ] || die "No shared_secret in provisioning response"

# ── Configure openwisp-config ─────────────────────────────────────────────────
info "Writing openwisp-config: url=$CONTROLLER_URL"
uci set openwisp.http.url="$CONTROLLER_URL"
uci set openwisp.http.shared_secret="$SHARED_SECRET"
[ -n "$ORG_UUID" ] && uci set openwisp.http.organization="$ORG_UUID"
uci commit openwisp

# Clear shared_secret from memory immediately after writing
unset SHARED_SECRET

# ── Optional: configure WireGuard ─────────────────────────────────────────────
WG_ENABLED=$(echo "$RESPONSE" | jsonfilter -e '@.wireguard.enabled' 2>/dev/null || echo "false")

if [ "$WG_ENABLED" = "true" ] && [ -f "$WG_PRIVKEY_FILE" ]; then
    info "Configuring WireGuard peer..."

    WG_SERVER_KEY=$(echo "$RESPONSE" | jsonfilter -e '@.wireguard.server_public_key')
    WG_ENDPOINT=$(echo   "$RESPONSE" | jsonfilter -e '@.wireguard.endpoint')
    WG_ALLOWED=$(echo    "$RESPONSE" | jsonfilter -e '@.wireguard.allowed_ips')
    WG_ADDRESS=$(echo    "$RESPONSE" | jsonfilter -e '@.wireguard.address' 2>/dev/null || echo "")
    WG_DNS=$(echo        "$RESPONSE" | jsonfilter -e '@.wireguard.dns'     2>/dev/null || echo "")
    WG_PRIVKEY=$(cat "$WG_PRIVKEY_FILE")

    # Remove any previous lullex WireGuard interface + peer
    uci -q delete network.lullex_wg      2>/dev/null || true
    uci -q delete network.wgpeer_lullex  2>/dev/null || true

    # Interface
    uci set network.lullex_wg=interface
    uci set network.lullex_wg.proto=wireguard
    uci set network.lullex_wg.private_key="$WG_PRIVKEY"
    [ -n "$WG_ADDRESS" ] && uci set network.lullex_wg.addresses="$WG_ADDRESS"
    [ -n "$WG_DNS"     ] && uci set network.lullex_wg.dns="$WG_DNS"

    # Peer
    uci set network.wgpeer_lullex=wireguard_lullex_wg
    uci set network.wgpeer_lullex.public_key="$WG_SERVER_KEY"
    uci set network.wgpeer_lullex.endpoint_host="${WG_ENDPOINT%%:*}"
    uci set network.wgpeer_lullex.endpoint_port="${WG_ENDPOINT##*:}"
    uci add_list network.wgpeer_lullex.allowed_ips="$WG_ALLOWED"
    uci set network.wgpeer_lullex.persistent_keepalive=25

    uci commit network
    unset WG_PRIVKEY
    info "WireGuard peer configured. Network restart required."
fi

# ── Security: erase token after successful adoption ───────────────────────────
# Uncomment if the token is single-use and you want the image wiped of it.
# uci set lullex-bootstrap.adopt.token=""
# uci commit lullex-bootstrap

# ── Restart services ──────────────────────────────────────────────────────────
info "Restarting openwisp-config..."
/etc/init.d/openwisp-config restart 2>/dev/null || true

if [ "$WG_ENABLED" = "true" ]; then
    info "Restarting network for WireGuard..."
    /etc/init.d/network restart 2>/dev/null || true
fi

echo "success" > "$STATUS_FILE"
info "Bootstrap complete."
