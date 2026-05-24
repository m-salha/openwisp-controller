VERSION = (1, 2, 3, "final")
__version__ = VERSION


def get_version():
    return f"{VERSION[0]}.{VERSION[1]}.{VERSION[2]}"


default_app_config = "openwisp_provisioning.apps.ProvisioningConfig"
