from django.conf import settings


def get_setting(name, default):
    return getattr(settings, f"OPENWISP_CONTROLLER_{name}", default)


# Optional override for the URL returned to the router as `openwisp.url`.
# If unset, the URL is built from the incoming request.
PUBLIC_URL = get_setting("PUBLIC_URL", "")

# Public defaults for the captive-portal block — these are not secrets
# and match the router-side contract. They can be overridden per-token
# in the admin.
DEFAULT_UAM_ALLOWED = get_setting("PROVISION_DEFAULT_UAM_ALLOWED", ["login.wifi.lullex.com"])
DEFAULT_CHILLI_NET = get_setting("PROVISION_DEFAULT_CHILLI_NET", "192.168.182.0/24")
DEFAULT_CHILLI_UAMLISTEN = get_setting("PROVISION_DEFAULT_CHILLI_UAMLISTEN", "192.168.182.1")
DEFAULT_CHILLI_UAMPORT = get_setting("PROVISION_DEFAULT_CHILLI_UAMPORT", "3990")
