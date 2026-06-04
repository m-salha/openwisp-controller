from django.contrib import admin

from openwisp_users.multitenancy import MultitenantOrgFilter

from ..admin import MultitenantAdminMixin
from .models import AdoptionToken


@admin.register(AdoptionToken)
class AdoptionTokenAdmin(MultitenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "description_or_id",
        "organization",
        "is_active",
        "use_count",
        "max_uses",
        "expires_at",
        "last_used_at",
        "created",
    )
    list_filter = (MultitenantOrgFilter, "is_active")
    search_fields = ("description", "organization__name", "last_used_mac")
    readonly_fields = (
        "id",
        "created",
        "modified",
        "use_count",
        "last_used_at",
        "last_used_mac",
    )
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "id",
                    "description",
                    "organization",
                    "token",
                    "is_active",
                )
            },
        ),
        (
            "Usage limits",
            {
                "fields": (
                    "max_uses",
                    "use_count",
                    "expires_at",
                    "last_used_at",
                    "last_used_mac",
                )
            },
        ),
        (
            "Captive portal / RADIUS",
            {
                "fields": (
                    "radius_server",
                    "radius_secret",
                    "uam_server",
                    "uam_allowed",
                    "chilli_net",
                    "chilli_uamlisten",
                    "chilli_uamport",
                ),
                "description": (
                    "Set radius_server, radius_secret and uam_server to "
                    "enable Chilli on the router. Leave any of them blank "
                    "to keep Chilli disabled."
                ),
            },
        ),
        (("Timestamps"), {"fields": ("created", "modified")}),
    )

    def description_or_id(self, obj):
        return obj.description or str(obj.id)

    description_or_id.short_description = "Token"
