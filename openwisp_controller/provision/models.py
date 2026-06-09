import swapper
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from openwisp_utils.base import KeyField, TimeStampedEditableModel

from . import settings as app_settings


class AdoptionToken(TimeStampedEditableModel):
    """
    Admin-managed token that a router presents at
    POST /api/lullex/provision/adopt/.

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
        # related_name is namespaced to avoid an E304/E305 reverse
        # accessor clash with the unrelated openwisp_provisioning app,
        # whose AdoptionToken.organization already uses "adoption_tokens".
        related_name="lullex_adoption_tokens",
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

    # Provisioning revision: bumped whenever any captive-portal / RADIUS
    # field below changes, so adopted routers can detect that their local
    # config is stale (via the check endpoint) and re-run adoption.
    provisioning_revision = models.PositiveIntegerField(
        _("provisioning revision"),
        default=1,
        help_text=_(
            "incremented automatically when captive-portal / RADIUS "
            "settings change"
        ),
    )
    revision_updated_at = models.DateTimeField(
        _("revision updated at"), null=True, blank=True
    )

    # Fields that drive the captive-portal / Chilli configuration handed
    # to routers. A change to any of these bumps provisioning_revision.
    PROVISIONING_FIELDS = (
        "radius_server",
        "radius_secret",
        "uam_server",
        "uam_allowed",
        "chilli_net",
        "chilli_uamlisten",
        "chilli_uamport",
    )

    class Meta:
        verbose_name = _("Adoption token")
        verbose_name_plural = _("Adoption tokens")
        ordering = ("-created",)

    def __str__(self):
        if self.description:
            return f"{self.description} ({self.organization})"
        return f"AdoptionToken {self.pk} ({self.organization})"

    def save(self, *args, **kwargs):
        """
        Auto-increment ``provisioning_revision`` when any provisioning
        field changes on an existing token. This is intentionally done in
        ``save()`` (not only in the admin) so the bump is consistent
        regardless of where the change originates. The adoption endpoint
        updates bookkeeping via queryset ``.update()`` and therefore never
        triggers this path.
        """
        if not self._state.adding and self.pk is not None:
            previous = (
                type(self)
                ._default_manager.filter(pk=self.pk)
                .only("provisioning_revision", *self.PROVISIONING_FIELDS)
                .first()
            )
            if previous is not None and any(
                getattr(previous, field) != getattr(self, field)
                for field in self.PROVISIONING_FIELDS
            ):
                self.provisioning_revision = (
                    previous.provisioning_revision or 0
                ) + 1
                self.revision_updated_at = timezone.now()
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = set(update_fields) | {
                        "provisioning_revision",
                        "revision_updated_at",
                    }
        super().save(*args, **kwargs)

    def check_validity(self):
        """
        Non-quota validity checks: active, organization active, and not
        expired. Returns (ok: bool, reason: str) where ``reason`` is a
        short, non-sensitive code suitable for logging.

        These checks apply to every adoption attempt, including the
        idempotent re-adoption of a MAC that was already adopted. The
        ``max_uses`` quota is intentionally NOT checked here so that an
        already-adopted router is never blocked by the quota; see
        ``is_usable`` for the full check used when a new slot is needed.
        """
        if not self.is_active:
            return False, "inactive"
        if not self.organization.is_active:
            return False, "org_inactive"
        if self.expires_at and self.expires_at <= timezone.now():
            return False, "expired"
        return True, ""

    def is_usable(self):
        """
        Full usability check, including the ``max_uses`` quota. Used when
        a brand-new usage slot is required (i.e. a MAC not yet adopted).
        Returns (ok: bool, reason: str).
        """
        ok, reason = self.check_validity()
        if not ok:
            return ok, reason
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


class AdoptionDeviceState(TimeStampedEditableModel):
    """
    Per-router provisioning state for a (token, MAC) pair.

    Tracks which ``provisioning_revision`` of the token has been applied
    to a given router, so an adopted OpenWrt device can detect (via the
    check endpoint) that its local captive-portal config is stale and
    re-run adoption. This model intentionally stores NO secrets.
    """

    STATUS_UNKNOWN = "unknown"
    STATUS_NEEDS_PROVISIONING = "needs_provisioning"
    STATUS_PROVISIONING = "provisioning"
    STATUS_PROVISIONED = "provisioned"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (
        (STATUS_UNKNOWN, _("unknown")),
        (STATUS_NEEDS_PROVISIONING, _("needs provisioning")),
        (STATUS_PROVISIONING, _("provisioning")),
        (STATUS_PROVISIONED, _("provisioned")),
        (STATUS_FAILED, _("failed")),
    )

    token = models.ForeignKey(
        "provision.AdoptionToken",
        verbose_name=_("adoption token"),
        related_name="device_states",
        on_delete=models.CASCADE,
    )
    mac_address = models.CharField(_("MAC address"), max_length=17)
    applied_revision = models.PositiveIntegerField(
        _("applied revision"), default=0
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_UNKNOWN,
    )
    last_error = models.CharField(
        _("last error"),
        max_length=255,
        blank=True,
        help_text=_("short, non-sensitive error description"),
    )
    last_seen_at = models.DateTimeField(
        _("last seen at"), null=True, blank=True
    )
    last_adopted_at = models.DateTimeField(
        _("last adopted at"), null=True, blank=True
    )

    class Meta:
        verbose_name = _("Adoption device state")
        verbose_name_plural = _("Adoption device states")
        unique_together = (("token", "mac_address"),)
        ordering = ("-modified",)

    def __str__(self):
        return (
            f"{self.mac_address} @ rev {self.applied_revision} "
            f"({self.status})"
        )
