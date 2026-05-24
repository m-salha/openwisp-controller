import ipaddress
import logging
import secrets

import swapper
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from openwisp_utils.base import UUIDModel

logger = logging.getLogger(__name__)


def _generate_token():
    """Return a 384-bit URL-safe token (~64 chars, high entropy)."""
    return secrets.token_urlsafe(48)


class AdoptionToken(UUIDModel):
    ACTIVE = "active"
    USED = "used"
    REVOKED = "revoked"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (ACTIVE, _("Active")),
        (USED, _("Used")),
        (REVOKED, _("Revoked")),
        (EXPIRED, _("Expired")),
    ]

    token = models.CharField(
        _("token"),
        max_length=128,
        unique=True,
        default=_generate_token,
        db_index=True,
    )
    organization = models.ForeignKey(
        swapper.get_model_name("openwisp_users", "Organization"),
        verbose_name=_("organization"),
        on_delete=models.CASCADE,
        related_name="adoption_tokens",
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=STATUS_CHOICES,
        default=ACTIVE,
        db_index=True,
    )
    max_uses = models.PositiveIntegerField(
        _("max uses"),
        null=True,
        blank=True,
        help_text=_("Leave blank for unlimited. Set to 1 for one-time tokens."),
    )
    uses_count = models.PositiveIntegerField(
        _("uses count"),
        default=0,
        editable=False,
    )
    expires_at = models.DateTimeField(
        _("expires at"),
        null=True,
        blank=True,
        help_text=_("Leave blank for no expiry."),
    )
    notes = models.TextField(_("notes"), blank=True)
    created = models.DateTimeField(_("created"), auto_now_add=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("Adoption Token")
        verbose_name_plural = _("Adoption Tokens")
        ordering = ["-created"]

    def __str__(self):
        return f"{self.organization} — {self.token[:20]}…"

    def validate(self):
        """
        Check whether this token may be used right now.
        Returns (is_valid: bool, error_reason: str | None).
        Expires the token in-place if it has passed its expiry date.
        """
        if self.status == self.REVOKED:
            return False, "Token has been revoked."
        if self.expires_at and timezone.now() > self.expires_at:
            if self.status != self.EXPIRED:
                AdoptionToken.objects.filter(pk=self.pk).update(status=self.EXPIRED)
            return False, "Token has expired."
        if self.status in (self.USED, self.EXPIRED):
            return False, f"Token is {self.status}."
        if self.status != self.ACTIVE:
            return False, "Token is not active."
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            AdoptionToken.objects.filter(pk=self.pk).update(status=self.USED)
            return False, "Token has reached its maximum uses."
        return True, None

    def consume(self):
        """Atomically increment uses_count; mark USED when max_uses is reached."""
        AdoptionToken.objects.filter(pk=self.pk).update(
            uses_count=models.F("uses_count") + 1,
        )
        self.refresh_from_db(fields=["uses_count"])
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            AdoptionToken.objects.filter(pk=self.pk).update(status=self.USED)


class OrganizationProvisioningConfig(UUIDModel):
    MODE_OPENWISP = "openwisp-config"
    MODE_VPN_RADIUS = "vpn-radius"
    MODE_CHOICES = [
        (MODE_OPENWISP, _("OpenWISP Config (shared_secret auto-registration)")),
        (MODE_VPN_RADIUS, _("VPN + RADIUS + Captive Portal")),
    ]

    organization = models.OneToOneField(
        swapper.get_model_name("openwisp_users", "Organization"),
        verbose_name=_("organization"),
        on_delete=models.CASCADE,
        related_name="provisioning_config",
    )
    enabled = models.BooleanField(_("enabled"), default=True)
    controller_url = models.URLField(
        _("controller URL"),
        help_text=_("e.g. https://controller.wifi.lullex.com"),
    )
    mode = models.CharField(
        _("mode"),
        max_length=32,
        choices=MODE_CHOICES,
        default=MODE_OPENWISP,
    )

    # ── WireGuard ────────────────────────────────────────────────────────────
    wireguard_server_public_key = models.CharField(
        _("WireGuard server public key"),
        max_length=64,
        blank=True,
    )
    wireguard_server_endpoint = models.CharField(
        _("WireGuard server endpoint"),
        max_length=255,
        blank=True,
        help_text=_("host:port — e.g. vpn.lullex.com:51820"),
    )
    wireguard_address_pool = models.CharField(
        _("WireGuard address pool"),
        max_length=64,
        blank=True,
        default="10.100.10.0/24",
        help_text=_("CIDR from which client tunnel IPs are allocated, e.g. 10.100.10.0/24"),
    )
    wireguard_allowed_ips = models.CharField(
        _("WireGuard allowed IPs"),
        max_length=255,
        blank=True,
        default="10.100.0.0/16",
        help_text=_("AllowedIPs written into the client peer config"),
    )
    wireguard_dns = models.CharField(
        _("WireGuard DNS"),
        max_length=255,
        blank=True,
    )

    # ── RADIUS ───────────────────────────────────────────────────────────────
    radius_server_ip = models.GenericIPAddressField(
        _("RADIUS server IP"),
        null=True,
        blank=True,
        help_text=_("Typically the WireGuard server tunnel IP, e.g. 10.100.0.1"),
    )
    radius_server_port = models.PositiveIntegerField(
        _("RADIUS auth port"),
        default=1812,
    )
    radius_acct_port = models.PositiveIntegerField(
        _("RADIUS acct port"),
        default=1813,
    )
    radius_secret = models.CharField(
        _("RADIUS NAS secret"),
        max_length=255,
        blank=True,
        help_text=_("Shared secret for NAS ↔ RADIUS. Keep this value secure."),
    )

    # ── Captive Portal (Coova-Chilli) ────────────────────────────────────────
    captive_portal_enabled = models.BooleanField(
        _("captive portal enabled"),
        default=False,
    )
    captive_portal_uam_server = models.CharField(
        _("UAM server URL"),
        max_length=512,
        blank=True,
        help_text=_("e.g. https://login.wifi.lullex.com/lullex/login"),
    )
    captive_portal_uam_port = models.PositiveIntegerField(
        _("UAM port"),
        default=3990,
    )
    captive_portal_uam_secret = models.CharField(
        _("UAM secret"),
        max_length=255,
        blank=True,
    )
    captive_portal_nas_id = models.CharField(
        _("NAS identifier"),
        max_length=64,
        blank=True,
    )
    captive_portal_dhcpif = models.CharField(
        _("DHCP interface"),
        max_length=32,
        default="br-lan",
    )
    captive_portal_tundev = models.CharField(
        _("tunnel device"),
        max_length=32,
        default="tun1",
    )

    created = models.DateTimeField(_("created"), auto_now_add=True)
    modified = models.DateTimeField(_("modified"), auto_now=True)

    class Meta:
        verbose_name = _("Organization Provisioning Config")
        verbose_name_plural = _("Organization Provisioning Configs")

    def __str__(self):
        return f"Provisioning config — {self.organization}"

    def allocate_wireguard_ip(self):
        """
        Find the first unused host IP inside wireguard_address_pool.
        Returns the IP string (without prefix) or None if pool is exhausted/unset.
        """
        if not self.wireguard_address_pool:
            return None
        try:
            network = ipaddress.ip_network(self.wireguard_address_pool, strict=False)
        except ValueError:
            logger.error("Invalid wireguard_address_pool: %s", self.wireguard_address_pool)
            return None
        used = set(
            AdoptedDevice.objects.filter(
                token__organization=self.organization,
                wireguard_assigned_ip__isnull=False,
            ).values_list("wireguard_assigned_ip", flat=True)
        )
        for host in network.hosts():
            ip_str = str(host)
            if ip_str not in used:
                return ip_str
        logger.warning("WireGuard address pool exhausted for org %s", self.organization_id)
        return None


class AdoptedDevice(UUIDModel):
    """Record of every device that was successfully provisioned via a token."""

    token = models.ForeignKey(
        AdoptionToken,
        verbose_name=_("adoption token"),
        on_delete=models.SET_NULL,
        null=True,
        related_name="adopted_devices",
    )
    mac_address = models.CharField(_("MAC address"), max_length=17, db_index=True)
    hostname = models.CharField(_("hostname"), max_length=64, blank=True)
    model = models.CharField(_("model"), max_length=64, blank=True)
    wireguard_public_key = models.CharField(
        _("WireGuard public key"),
        max_length=64,
        blank=True,
    )
    wireguard_assigned_ip = models.GenericIPAddressField(
        _("WireGuard assigned IP"),
        null=True,
        blank=True,
    )
    adopted_at = models.DateTimeField(_("adopted at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Adopted Device")
        verbose_name_plural = _("Adopted Devices")
        ordering = ["-adopted_at"]

    def __str__(self):
        label = self.hostname or self.mac_address
        return f"{label} (adopted {self.adopted_at:%Y-%m-%d})"
