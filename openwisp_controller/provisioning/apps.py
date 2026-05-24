from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProvisioningConfig(AppConfig):
    name = 'openwisp_controller.provisioning'
    label = 'provisioning'
    verbose_name = _('Provisioning')
