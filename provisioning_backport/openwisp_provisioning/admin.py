from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _

from .models import AdoptedDevice, AdoptionToken, OrganizationProvisioningConfig


class AdoptedDeviceInline(admin.TabularInline):
    model = AdoptedDevice
    extra = 0
    readonly_fields = (
        "mac_address",
        "hostname",
        "model",
        "wireguard_public_key",
        "wireguard_assigned_ip",
        "adopted_at",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AdoptionToken)
class AdoptionTokenAdmin(admin.ModelAdmin):
    list_display = (
        "token_preview",
        "organization",
        "status",
        "uses_count",
        "max_uses",
        "expires_at",
        "created",
    )
    list_filter = ("status", "organization")
    search_fields = ("token", "organization__name", "notes")
    readonly_fields = ("token", "uses_count", "created", "modified")
    actions = ["revoke_tokens"]
    inlines = [AdoptedDeviceInline]

    fieldsets = (
        (None, {
            "fields": ("organization", "token", "status", "notes"),
        }),
        (_("Usage limits"), {
            "fields": ("max_uses", "uses_count", "expires_at"),
            "description": _(
                "Set max_uses=1 for single-use (one-time) tokens. "
                "Leave max_uses blank for unlimited reuse."
            ),
        }),
        (_("Timestamps"), {
            "fields": ("created", "modified"),
            "classes": ("collapse",),
        }),
    )

    def token_preview(self, obj):
        return f"{obj.token[:24]}…"

    token_preview.short_description = _("Token (preview)")

    def revoke_tokens(self, request, queryset):
        revoked = queryset.filter(status=AdoptionToken.ACTIVE).update(
            status=AdoptionToken.REVOKED
        )
        self.message_user(
            request,
            f"{revoked} token(s) revoked.",
            messages.SUCCESS,
        )

    revoke_tokens.short_description = _("Revoke selected tokens")


@admin.register(OrganizationProvisioningConfig)
class OrganizationProvisioningConfigAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "enabled",
        "mode",
        "controller_url",
        "captive_portal_enabled",
        "modified",
    )
    list_filter = ("enabled", "mode", "captive_portal_enabled")
    search_fields = ("organization__name", "controller_url")

    fieldsets = (
        (None, {
            "fields": ("organization", "enabled", "controller_url", "mode"),
        }),
        (_("WireGuard VPN"), {
            "fields": (
                "wireguard_server_public_key",
                "wireguard_server_endpoint",
                "wireguard_address_pool",
                "wireguard_allowed_ips",
                "wireguard_dns",
            ),
            "classes": ("collapse",),
            "description": _(
                "Leave blank to omit WireGuard config from provisioning responses."
            ),
        }),
        (_("RADIUS"), {
            "fields": (
                "radius_server_ip",
                "radius_server_port",
                "radius_acct_port",
                "radius_secret",
            ),
            "classes": ("collapse",),
            "description": _(
                "radius_server_ip should be the WireGuard tunnel IP of the "
                "RADIUS server (e.g. 10.100.0.1) when WireGuard is active."
            ),
        }),
        (_("Captive Portal — Coova-Chilli"), {
            "fields": (
                "captive_portal_enabled",
                "captive_portal_uam_server",
                "captive_portal_uam_port",
                "captive_portal_uam_secret",
                "captive_portal_nas_id",
                "captive_portal_dhcpif",
                "captive_portal_tundev",
            ),
            "classes": ("collapse",),
            "description": _(
                "papalwaysok and nochallenge are always included when captive_portal_enabled=True. "
                "chap=0, uamallowed=0.0.0.0/0 and mschapv2 are never sent."
            ),
        }),
    )
