# openwisp-provisioning (1.2.x backport)

SaaS device adoption/provisioning layer for OpenWISP, packaged as a
**standalone Django app** that installs **on top of an existing
openwisp-controller 1.2.3 environment** without upgrading any OpenWISP
dependency to the 1.3 line.

## Why a standalone package?

The 1.3 development branch pins `openwisp-users`, `openwisp-utils`,
`openwisp-ipam` and `openwisp-notifications` to their `1.3` branches.
Installing it on a 1.2.3 server forces those upgrades and breaks the
dependency tree.

This package avoids that entirely:

- It does **not** reinstall `openwisp-controller`.
- It declares only `django`, `djangorestframework` and `swapper` as
  dependencies (all already present in a 1.2.3 install).
- It references the already-installed `openwisp_users.Organization` and
  `config_settings.shared_secret` at **runtime** via `swapper` — no version
  pin, no upgrade.

Verified compatible with: `openwisp-controller==1.2.3`,
`openwisp-users==1.2.2`, `openwisp-utils==1.2.2`, Django 4.2–5.2.

---

## Deployment on the production server

### 1. Install the package (no dependency upgrades)

```bash
# Activate the same virtualenv the production OpenWISP runs in, then:
pip install \
  "git+https://github.com/m-salha/openwisp-controller.git@claude/provisioning-backport-1.2.3#subdirectory=provisioning_backport"
```

To prove no OpenWISP package is touched, dry-run first:

```bash
pip install --dry-run \
  "git+https://github.com/m-salha/openwisp-controller.git@claude/provisioning-backport-1.2.3#subdirectory=provisioning_backport"
# Expect: only openwisp-provisioning to be collected.
# openwisp-users / openwisp-utils / openwisp-controller must NOT appear as upgrades.
```

Alternative — pin the exact commit for reproducible deploys:

```bash
pip install \
  "git+https://github.com/m-salha/openwisp-controller.git@<COMMIT_SHA>#subdirectory=provisioning_backport"
```

### 2. Register the app

In your project settings (e.g. `openwisp2/settings.py`), add to
`INSTALLED_APPS` — it must come **after** `openwisp_users`:

```python
INSTALLED_APPS += [
    "openwisp_provisioning",
]
```

### 3. Wire up the URL

In your project `urls.py`:

```python
urlpatterns += [
    path("", include("openwisp_provisioning.api.urls")),
]
# exposes POST /api/provision/adopt/
```

### 4. Apply migrations

```bash
python manage.py migrate provisioning
```

### 5. (Recommended) Tighten the rate limit

The adoption endpoint defaults to 10 requests/min/IP. Override in settings:

```python
REST_FRAMEWORK = {
    # ...existing config...
    "DEFAULT_THROTTLE_RATES": {
        # ...existing rates...
        "adoption": "10/min",
    },
}
```

### 6. Restart services

```bash
sudo systemctl restart openwisp-uwsgi    # or gunicorn / daphne, per your setup
```

---

## Verifying the install didn't change OpenWISP

```bash
pip show openwisp-controller openwisp-users openwisp-utils \
  | grep -E "Name|Version"
# Expect unchanged: 1.2.3 / 1.2.2 / 1.2.2
```

---

## What's included

| Component | Path |
|-----------|------|
| Models (AdoptionToken, OrganizationProvisioningConfig, AdoptedDevice) | `openwisp_provisioning/models.py` |
| Admin (token create/list/revoke, linked devices) | `openwisp_provisioning/admin.py` |
| API `POST /api/provision/adopt/` | `openwisp_provisioning/api/` |
| Migration | `openwisp_provisioning/migrations/0001_initial.py` |
| Tests (model + API) | `openwisp_provisioning/tests/` |
| OpenWrt bootstrap script + image guide | `openwisp_provisioning/bootstrap/` |

The models, admin, API behaviour, security properties (vague errors, rate
limiting, high-entropy tokens, no secret logging, one-time/limited-use
tokens) and the OpenWrt bootstrap tooling are identical to the 1.3 version —
only the Python import path changed from `openwisp_controller.provisioning`
to `openwisp_provisioning`.

---

## Running the test suite

The tests require a Django project with `openwisp_users` configured (the
production project already provides this). From a checkout:

```bash
cd provisioning_backport
DJANGO_SETTINGS_MODULE=<your_test_settings> \
  python -m django test openwisp_provisioning
```

See `openwisp_provisioning/bootstrap/README.md` for the full OpenWrt image
build guide, Coova-Chilli constraints, and API reference.
