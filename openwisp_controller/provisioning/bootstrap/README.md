# Generic OpenWrt Image — Bootstrap Guide

This guide explains how to build a **tenant-agnostic** OpenWrt image that
contains **no** organisation-specific secrets, and how to provision it
per-tenant on first boot using the Lullex Provisioning API.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Generic OpenWrt image  (no secrets, same for every customer)    │
│   /usr/sbin/lullex-adopt   ← bootstrap script                  │
│   /etc/config/lullex-bootstrap  ← adoption token only          │
└───────────────────────┬─────────────────────────────────────────┘
                        │ POST /api/provision/adopt/
                        ▼
┌───────────────────────────────────────────────────────────────── ┐
│ Lullex Provisioning API                                         │
│   • Validates token                                             │
│   • Returns: controller_url, shared_secret, WireGuard, RADIUS, │
│              Captive Portal config                              │
└───────────────────────┬─────────────────────────────────────────┘
                        │ writes /etc/config/openwisp
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ openwisp-config registers device with OpenWISP controller       │
│ Controller pushes VPN + RADIUS + Captive Portal templates       │
└─────────────────────────────────────────────────────────────────┘
```

---

## What the generic image contains

| Item | Included? | Notes |
|------|-----------|-------|
| OpenWrt base system | ✅ | |
| openwisp-config | ✅ | controller URL + shared_secret written at boot |
| lullex-adopt script | ✅ | `/usr/sbin/lullex-adopt` |
| Adoption token | ✅ (per batch) | Written to `/etc/config/lullex-bootstrap` before flashing |
| shared_secret | ❌ | Received from API at first boot |
| RADIUS secret | ❌ | Pushed by OpenWISP template after registration |
| WireGuard private key | ❌ | Generated on-device at first boot |
| Captive portal final config | ❌ | Pushed by OpenWISP template |

---

## Step-by-step deployment

### 1. Issue an adoption token in the Django admin

1. Go to **Provisioning → Adoption Tokens → Add**.
2. Select the target **organisation**.
3. Set **max uses** = 1 (one-time token) or the number of devices in the batch.
4. Set an **expiry** date appropriate for deployment window.
5. Save — copy the generated token string.

### 2. Configure the provisioning profile (once per org)

1. Go to **Provisioning → Organization Provisioning Configs → Add**.
2. Fill in **controller URL**, **WireGuard**, **RADIUS**, and **Captive Portal** fields.
3. Save.

### 3. Prepare the image

Write `/etc/config/lullex-bootstrap` **before** flashing or at factory time:

```
config adopt 'adopt'
    option url    'https://controller.wifi.lullex.com/api/provision/adopt/'
    option token  'YOUR_ADOPTION_TOKEN_HERE'
    option verify_ssl '1'
```

Install the bootstrap script:

```
cp openwrt-adopt.sh /usr/sbin/lullex-adopt
chmod 755 /usr/sbin/lullex-adopt
```

Add a procd init script so it runs once at first boot:

```sh
#!/bin/sh /etc/rc.common
START=99
USE_PROCD=1

start_service() {
    /usr/sbin/lullex-adopt
}
```

Save as `/etc/init.d/lullex-adopt`, then `chmod 755` and `enable` it.

### 4. Flash and power on

The device boots → `lullex-adopt` runs → contacts the API → writes
`/etc/config/openwisp` → restarts openwisp-config → device appears in the
OpenWISP dashboard → controller pushes the remaining templates (WireGuard
VPN, RADIUS, Captive Portal via Coova-Chilli UCI templates).

---

## Captive Portal notes (Coova-Chilli)

The provisioning API response always includes `papalwaysok: true` and
`nochallenge: true`.  The OpenWISP **template** that is pushed post-adoption
must set:

```
option dhcpif    br-lan
option tundev    tun1
option radiusserver1  <WireGuard tunnel IP of RADIUS server>
option radiussecret   <from tenant config — NOT in bootstrap response>
```

The following options **must not** appear in the template:

- `chap 0`
- `uamallowed 0.0.0.0/0`
- `mschapv2` (unless explicitly required)
- `uamsecret` (unless UAM secret is actually needed)

---

## Security notes

| Concern | Mitigation |
|---------|------------|
| Token interception | Use HTTPS only; set short expiry |
| Token reuse | Set `max_uses=1` for one-time tokens |
| Secret exposure in logs | API never logs `shared_secret` or `radius_secret` |
| Stale token on device | Uncomment erase lines in `openwrt-adopt.sh` after adoption |
| WireGuard key confidentiality | Private key generated on-device, never transmitted |
| Rate limiting | Adoption endpoint limited to 10 req/min/IP by default |

---

## API reference

**POST** `/api/provision/adopt/`

Request:
```json
{
  "token": "abc123…",
  "mac_address": "08:00:27:6A:D1:87",
  "hostname": "openwrt-device",
  "model": "x86_64-vbox",
  "public_wireguard_key": "<optional base64 WG pubkey>"
}
```

Response:
```json
{
  "controller_url": "https://controller.wifi.lullex.com",
  "organization": "Lullex",
  "organization_uuid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "mode": "openwisp-config",
  "shared_secret": "…",
  "wireguard": {
    "enabled": true,
    "server_public_key": "…",
    "endpoint": "vpn.lullex.com:51820",
    "address": "10.100.10.2/32",
    "allowed_ips": "10.100.0.0/16"
  },
  "radius": {
    "server": "10.100.0.1",
    "auth_port": 1812,
    "acct_port": 1813,
    "secret": "…"
  },
  "captive_portal": {
    "enabled": true,
    "uamserver": "https://login.wifi.lullex.com/lullex/login",
    "uamport": 3990,
    "dhcpif": "br-lan",
    "tundev": "tun1",
    "papalwaysok": true,
    "nochallenge": true
  }
}
```

Error responses: `401` (invalid/expired token), `503` (provisioning not
configured), `400` (validation error), `429` (rate limit exceeded).

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `401 Invalid or expired token` | Token revoked, expired, or already fully used |
| `503 Provisioning not configured` | No `OrganizationProvisioningConfig` for that org |
| Device not appearing in OpenWISP | Check `/tmp/lullex-adopt.status` and `logread -e lullex-adopt` |
| WireGuard not coming up | Verify `wg` is installed; check pool exhaustion in admin |
| `curl: (60) SSL certificate problem` | Set `verify_ssl 0` in bootstrap config for lab only |
