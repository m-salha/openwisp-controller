import json
import logging
import re

from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from swapper import load_model

from . import settings as app_settings
from .models import AdoptionToken

Device = load_model("config", "Device")
OrganizationConfigSettings = load_model("config", "OrganizationConfigSettings")

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")


def _json_error(message, status):
    return JsonResponse({"error": message}, status=status)


def _resolve_openwisp_url(request):
    """
    The URL the router uses to talk back to the controller (used as
    `openwisp.url`). Prefers the OPENWISP_CONTROLLER_PUBLIC_URL setting
    if set; otherwise falls back to the request's absolute URI root.
    """
    if app_settings.PUBLIC_URL:
        return app_settings.PUBLIC_URL.rstrip("/") + "/"
    return request.build_absolute_uri("/")


@method_decorator(csrf_exempt, name="dispatch")
class AdoptView(View):
    """
    POST /api/lullex/provision/adopt/

    Request body (JSON):
        {
          "token": "...",
          "mac_address": "...",
          "hostname": "...",
          "model": "...",
          "agent_version": "..."
        }

    Response body (JSON, 200):
        {
          "openwisp": {"url": "...", "shared_secret": "..."},
          "chilli": { ... }
        }

    Security notes:
      * The token value, the response body, and any RADIUS/OpenWISP
        secret are NEVER logged. Only the token UUID, organization id
        and (best-effort) MAC address are logged for traceability.
      * Validation failures return a generic error code; the reason is
        only included in logs.
      * If the MAC is already claimed by a different organization the
        request is rejected with HTTP 403 and the response does not
        include the organization's shared_secret.
      * Adoption is idempotent per MAC: a MAC already adopted in the
        token's organization (i.e. a Device row already exists) may
        re-adopt freely. Re-adoption does NOT consume a use_count slot
        and is allowed even once max_uses has been reached, so routers
        can re-run adoption periodically to pick up GUI changes. Only a
        new MAC consumes a slot and is subject to max_uses.
      * The validity check and the use_count increment run inside a
        single transaction with a row-level lock (select_for_update),
        and the increment itself is a conditional UPDATE guarded by an
        F()-expression, so max_uses cannot be bypassed by concurrent
        adoption requests even on backends where SELECT ... FOR UPDATE
        is a no-op (e.g. SQLite).
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            logger.warning("provision.adopt: malformed JSON body")
            return _json_error("malformed JSON", status=400)

        if not isinstance(payload, dict):
            return _json_error("malformed JSON", status=400)

        token_value = payload.get("token")
        mac_address = payload.get("mac_address")
        if not token_value or not mac_address:
            logger.warning(
                "provision.adopt: missing required field(s) "
                "(token_present=%s, mac_present=%s)",
                bool(token_value),
                bool(mac_address),
            )
            return _json_error(
                "missing required fields: token, mac_address", status=400
            )

        if not _MAC_RE.match(mac_address):
            logger.warning("provision.adopt: invalid mac_address shape")
            return _json_error("invalid mac_address", status=400)
        mac_address = mac_address.upper()

        # Critical section: lock the token row, validate, claim a usage
        # slot, and (best-effort) pre-register the device — all atomic.
        with transaction.atomic():
            try:
                token = (
                    AdoptionToken.objects.select_for_update()
                    .select_related("organization")
                    .get(token=token_value)
                )
            except AdoptionToken.DoesNotExist:
                logger.warning(
                    "provision.adopt: unknown token (mac=%s)", mac_address
                )
                return _json_error("invalid token", status=403)

            # Base validity (active / org active / not expired) applies
            # to every adoption attempt, including idempotent re-adoption.
            # The max_uses quota is handled separately below so that an
            # already-adopted MAC is never blocked by it.
            ok, reason = token.check_validity()
            if not ok:
                logger.warning(
                    "provision.adopt: token %s not usable (%s, mac=%s)",
                    token.pk,
                    reason,
                    mac_address,
                )
                return _json_error("invalid token", status=403)

            organization = token.organization

            # MAC ownership / idempotency. Must be resolved BEFORE we
            # touch the organization's shared_secret, so a router whose
            # MAC is already claimed by another tenant cannot extract
            # this tenant's secret by presenting a valid token.
            #   * owned by a DIFFERENT org -> 403
            #   * already present in THIS org -> re-adoption (no slot)
            #   * unknown MAC -> new adoption (consumes one slot)
            mac_owner_org_id = (
                Device.objects.filter(mac_address=mac_address)
                .values_list("organization_id", flat=True)
                .first()
            )
            if (
                mac_owner_org_id is not None
                and mac_owner_org_id != organization.id
            ):
                logger.warning(
                    "provision.adopt: mac %s already owned by org %s "
                    "(token org %s)",
                    mac_address,
                    mac_owner_org_id,
                    organization.id,
                )
                return _json_error(
                    "mac address already claimed", status=403
                )
            is_readoption = mac_owner_org_id is not None

            try:
                org_settings = OrganizationConfigSettings.objects.only(
                    "shared_secret"
                ).get(organization=organization)
            except OrganizationConfigSettings.DoesNotExist:
                logger.error(
                    "provision.adopt: organization %s missing config_settings",
                    organization.pk,
                )
                return _json_error("configuration unavailable", status=503)

            shared_secret = org_settings.shared_secret
            if not shared_secret:
                logger.error(
                    "provision.adopt: organization %s has empty shared_secret",
                    organization.pk,
                )
                return _json_error("configuration unavailable", status=503)

            if is_readoption:
                # Idempotent re-adoption: the router (MAC) was already
                # adopted in this organization, so do NOT consume another
                # use_count slot. Only refresh bookkeeping. This is
                # permitted even when max_uses has been reached.
                AdoptionToken.objects.filter(pk=token.pk).update(
                    last_used_at=timezone.now(),
                    last_used_mac=mac_address,
                )
            else:
                # New MAC: atomically reserve one slot. Even on backends
                # where select_for_update is a no-op, the conditional
                # UPDATE guarantees max_uses cannot be exceeded: only a
                # caller below the cap will see rows == 1.
                update_filters = {"pk": token.pk}
                if token.max_uses is not None:
                    update_filters["use_count__lt"] = token.max_uses
                rows = AdoptionToken.objects.filter(**update_filters).update(
                    use_count=F("use_count") + 1,
                    last_used_at=timezone.now(),
                    last_used_mac=mac_address,
                )
                if rows == 0:
                    logger.warning(
                        "provision.adopt: token %s has no free slot "
                        "(mac=%s)",
                        token.pk,
                        mac_address,
                    )
                    transaction.set_rollback(True)
                    return _json_error("invalid token", status=403)

                # We hold the slot — safe to persist the device row.
                self._register_device(organization, mac_address, payload)

        openwisp_url = _resolve_openwisp_url(request)
        response_body = {
            "openwisp": {
                "url": openwisp_url,
                "shared_secret": shared_secret,
            },
            "chilli": token.chilli_block(),
        }
        # Intentionally log no secrets and no response body.
        logger.info(
            "provision.adopt: success token=%s org=%s mac=%s readoption=%s",
            token.pk,
            organization.pk,
            mac_address,
            int(is_readoption),
        )
        return JsonResponse(response_body, status=200)

    def _register_device(self, organization, mac_address, payload):
        """
        Best-effort: create a Device row for this MAC inside the token's
        organization if one does not already exist. Cross-organization
        ownership is rejected by the caller before this is invoked.
        Failures here do not block the adoption response — the router
        can still self-register via the existing controller/register/
        endpoint.
        """
        try:
            if Device.objects.filter(mac_address=mac_address).exists():
                return
            hostname = (payload.get("hostname") or "").strip()
            model = (payload.get("model") or "").strip()
            name = hostname or mac_address
            device = Device(
                name=name[:64],
                organization=organization,
                mac_address=mac_address,
                model=model[:64],
            )
            device.full_clean()
            device.save()
        except Exception as exc:  # noqa: BLE001
            # Never echo the payload or any sensitive field.
            logger.warning(
                "provision.adopt: device pre-registration skipped (%s)",
                exc.__class__.__name__,
            )


adopt = AdoptView.as_view()
