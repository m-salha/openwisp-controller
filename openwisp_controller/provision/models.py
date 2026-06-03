import swapper
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openwisp_utils.base import KeyField, TimeStampedEditableModel

from . import settings as app_settings


class AdoptionToken(TimeStampedEditableModel):
    """
    Admin-managed token that a router presents at POST /api/provision/adopt/.

    A token is bound to one organization. On successful validation the
    controller returns:
      * the OpenWISP registration URL and the organization's existing
        shared_secret (so the router can self-register against the
        existing controller/register/ endpoint);
      * the captive-portal / RADIUS settings to drive Chilli.

    Tenant secrets (radius_server, radius_secret, uam_server) are stored
    here per-token and are NOT hardcoded anywhere in the codebase.
    """

    token = KeyField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name=_("token"),
        help_text=_("secret value presented by the router at adoption time"),
    )
    description = models.CharField(
        _("description"),
        max_length=128,
        blank=True,
        help_text=_("admin-visible label for this token"),
    )
    organization = models.ForeignKey(
        swapper.get_model_name("openwisp_users", "Organization"),
        verbose_name=_("organization"),
        related_name="adoption_tokens",
        on_delete=models.CASCADE,
    )
    is_active = models.BooleanField(_("active"), default=True)
    max_uses = models.PositiveIntegerField(
        _("max uses"),
        null=True,
        blank=True,
        help_text=_("leave blank for unlimited"),
    )
    use_count = models.PositiveIntegerField(_("use count"), default=0)
    expires_at = models.DateTimeField(_("expires at"), null=True, blank=True)
    last_used_at = models.DateTimeField(_("last used at"), null=True, blank=True)
    last_used_mac = models.CharField(
        _("last used by MAC"), max_length=17, blank=True
    )

    # Per-tenant Chilli / RADIUS configuration. These three are sensitive
    # and intentionally have no defaults: if any is blank the router will
    # not receive a complete Chilli block and will keep Chilli disabled.
    radius_server = models.CharField(
        _("RADIUS server"), max_length=255, blank=True
    )
    radius_secret = models.CharField(
        _("RADIUS shared secret"), max_length=255, blank=True
    )
    uam_server = models.CharField(
        _("UAM server URL"), max_length=255, blank=True
    )

    # Non-secret captive-portal defaults; admin-overridable per token.
    uam_allowed = models.JSONField(
        _("UAM allowed domains"),
        default=list,
        blank=True,
        help_text=_("list of allow-listed captive-portal hostnames"),
    )
    chilli_net = models.CharField(
        _("Chilli network"),
        max_length=64,
        blank=True,
        default="",
    )
    chilli_uamlisten = models.CharField(
        _("Chilli UAM listen address"),
        max_length=64,
        blank=True,
        default="",
    )
    chilli_uamport = models.CharField(
        _("Chilli UAM port"),
        max_length=10,
        blank=True,
        default="",
    )

    class Meta:
        verbose_name = _("Adoption token")
        verbose_name_plural = _("Adoption tokens")
        ordering = ("-created",)

    def __str__(self):
        if self.description:
            return f"{self.description} ({self.organization})"
        return f"AdoptionToken {self.pk} ({self.organization})"

    def is_usable(self):
        """
        Returns (ok: bool, reason: str). `reason` is a short, non-sensitive
        code suitable for logging.
        """
        if not self.is_active:
            return False, "inactive"
        if not self.organization.is_active:
            return False, "org_inactive"
        if self.expires_at and self.expires_at <= timezone.now():
            return False, "expired"
        if self.max_uses is not None and self.use_count >= self.max_uses:
            return False, "max_uses_reached"
        return True, ""

    def chilli_block(self):
        """
        Build the chilli block of the adoption response.

        The three tenant-secret fields (radius_server, radius_secret,
        uam_server) are only added if ALL three are configured. If any
        is missing the router intentionally won't get them and will
        keep Chilli disabled.
        """
        block = {
            "uamallowed": list(self.uam_allowed)
            if self.uam_allowed
            else list(app_settings.DEFAULT_UAM_ALLOWED),
            "net": self.chilli_net or app_settings.DEFAULT_CHILLI_NET,
            "uamlisten": self.chilli_uamlisten
            or app_settings.DEFAULT_CHILLI_UAMLISTEN,
            "uamport": self.chilli_uamport
            or app_settings.DEFAULT_CHILLI_UAMPORT,
        }
        if self.radius_server and self.radius_secret and self.uam_server:
            block["radiusserver1"] = self.radius_server
            block["radiussecret"] = self.radius_secret
            block["uamserver"] = self.uam_server
        return block
