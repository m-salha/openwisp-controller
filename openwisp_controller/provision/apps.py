from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProvisionConfig(AppConfig):
    name = "openwisp_controller.provision"
    label = "provision"
    verbose_name = _("Router Provisioning")
    default_auto_field = "django.db.models.AutoField"
