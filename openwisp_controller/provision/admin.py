from django.contrib import admin

from openwisp_users.multitenancy import MultitenantOrgFilter

from ..admin import MultitenantAdminMixin
from .models import AdoptionDeviceState, AdoptionToken


@admin.register(AdoptionToken)
class AdoptionTokenAdmin(MultitenantAdminMixin, admin.ModelAdmin):
    list_display = (
        "description_or_id",
        "organization",
        "is_active",
        "use_count",
        "max_uses",
        "provisioning_revision",
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
        "provisioning_revision",
        "revision_updated_at",
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
        (
            "Provisioning",
            {
                "fields": (
                    "provisioning_revision",
                    "revision_updated_at",
                ),
                "description": (
                    "Revision is incremented automatically when the "
                    "captive-portal / RADIUS settings above change. "
                    "Adopted routers compare it against their applied "
                    "revision to decide whether to re-run adoption."
                ),
            },
        ),
        (("Timestamps"), {"fields": ("created", "modified")}),
    )

    def description_or_id(self, obj):
        return obj.description or str(obj.id)

    description_or_id.short_description = "Token"


@admin.register(AdoptionDeviceState)
class AdoptionDeviceStateAdmin(admin.ModelAdmin):
    """
    Read-only view of per-router provisioning state. Controller-managed
    (created/updated by the adopt and check endpoints), so adding or
    editing rows by hand is disabled. Exposes no secrets.
    """

    list_display = (
        "mac_address",
        "token",
        "organization",
        "status",
        "applied_revision",
        "last_seen_at",
        "last_adopted_at",
    )
    list_filter = ("status",)
    search_fields = ("mac_address", "token__description")
    list_select_related = ("token", "token__organization")
    ordering = ("-modified",)
    readonly_fields = (
        "id",
        "token",
        "mac_address",
        "applied_revision",
        "status",
        "last_error",
        "last_seen_at",
        "last_adopted_at",
        "created",
        "modified",
    )

    def organization(self, obj):
        return obj.token.organization

    organization.short_description = "organization"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
