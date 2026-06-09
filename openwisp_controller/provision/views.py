import json
import logging
import re

from django.db import transaction
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View
from swapper import load_model

from . import settings as app_settings
from .models import AdoptionDeviceState, AdoptionToken

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


def _parse_request(request, log_prefix):
    """
    Shared request parsing/validation for the adopt and check endpoints.

    Returns ``(parsed, error)`` where ``parsed`` is
    ``(payload, token_value, mac_address)`` with the MAC normalized to
    upper case, or ``(None, JsonResponse)`` on failure. Never logs the
    token, the MAC, or the payload body.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        logger.warning("%s: malformed JSON body", log_prefix)
        return None, _json_error("malformed JSON", status=400)

    if not isinstance(payload, dict):
        return None, _json_error("malformed JSON", status=400)

    token_value = payload.get("token")
    mac_address = payload.get("mac_address")
    if not token_value or not mac_address:
        logger.warning(
            "%s: missing required field(s) "
            "(token_present=%s, mac_present=%s)",
            log_prefix,
            bool(token_value),
            bool(mac_address),
        )
        return None, _json_error(
            "missing required fields: token, mac_address", status=400
        )

    if not _MAC_RE.match(mac_address):
        logger.warning("%s: invalid mac_address shape", log_prefix)
        return None, _json_error("invalid mac_address", status=400)

    return (payload, token_value, mac_address.upper()), None


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
          "chilli": { ... },
          "provisioning": {"revision": <int>, "status": "provisioned"}
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
        and is allowed once max_uses has been reached, so routers can
        re-run adoption periodically to pick up GUI changes. Only a new
        MAC consumes a slot and is subject to max_uses.
      * The validity check and the use_count increment run inside a
        single transaction with a row-level lock (select_for_update),
        and the increment itself is a conditional UPDATE guarded by an
        F()-expression, so max_uses cannot be bypassed by concurrent
        adoption requests even on backends where SELECT ... FOR UPDATE
        is a no-op (e.g. SQLite).
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        parsed, error = _parse_request(request, "provision.adopt")
        if error is not None:
            return error
        payload, token_value, mac_address = parsed

        # Critical section: lock the token row, validate, claim a usage
        # slot, pre-register the device, and record provisioning state —
        # all atomic.
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

            now = timezone.now()
            if is_readoption:
                # Idempotent re-adoption: the router (MAC) was already
                # adopted in this organization, so do NOT consume another
                # use_count slot. Only refresh bookkeeping. This is
                # permitted even when max_uses has been reached.
                AdoptionToken.objects.filter(pk=token.pk).update(
                    last_used_at=now,
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
                    last_used_at=now,
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

            # Record/refresh per-router provisioning state. This marks the
            # router as having the current revision applied. It never
            # touches use_count, so idempotency is preserved.
            AdoptionDeviceState.objects.update_or_create(
                token=token,
                mac_address=mac_address,
                defaults={
                    "applied_revision": token.provisioning_revision,
                    "status": AdoptionDeviceState.STATUS_PROVISIONED,
                    "last_error": "",
                    "last_seen_at": now,
                    "last_adopted_at": now,
                },
            )

        openwisp_url = _resolve_openwisp_url(request)
        response_body = {
            "openwisp": {
                "url": openwisp_url,
                "shared_secret": shared_secret,
            },
            "chilli": token.chilli_block(),
            "provisioning": {
                "revision": token.provisioning_revision,
                "status": AdoptionDeviceState.STATUS_PROVISIONED,
            },
        }
        # Intentionally log no secrets and no response body.
        logger.info(
            "provision.adopt: success token=%s org=%s mac=%s readoption=%s "
            "revision=%s",
            token.pk,
            organization.pk,
            mac_address,
            int(is_readoption),
            token.provisioning_revision,
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


@method_decorator(csrf_exempt, name="dispatch")
class CheckView(View):
    """
    POST /api/lullex/provision/check/

    Lightweight, read-mostly endpoint that lets an adopted router learn
    whether its captive-portal config is stale relative to the token's
    current provisioning_revision, WITHOUT consuming a use_count slot and
    WITHOUT returning any secret.

    Request body (JSON):
        {
          "token": "...",
          "mac_address": "...",
          "hostname": "... optional",
          "model": "... optional",
          "agent_version": "... optional"
        }

    Response body (JSON, 200):
        {
          "provisioning": {
            "current_revision": <int>,
            "applied_revision": <int>,
            "needs_adoption": <bool>,
            "status": "needs_provisioning" | "provisioned"
          }
        }

    Security notes:
      * Returns no secrets: no token, shared_secret, radius_secret,
        uam_server, or chilli block — only revision integers, a boolean
        and a status string.
      * Never logs the token, MAC payload, or any secret.
      * Never increments use_count and never creates Device rows.
    """

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        parsed, error = _parse_request(request, "provision.check")
        if error is not None:
            return error
        _payload, token_value, mac_address = parsed

        try:
            token = AdoptionToken.objects.select_related("organization").get(
                token=token_value
            )
        except AdoptionToken.DoesNotExist:
            logger.warning(
                "provision.check: unknown token (mac=%s)", mac_address
            )
            return _json_error("invalid token", status=403)

        ok, reason = token.check_validity()
        if not ok:
            logger.warning(
                "provision.check: token %s not usable (%s, mac=%s)",
                token.pk,
                reason,
                mac_address,
            )
            return _json_error("invalid token", status=403)

        current_revision = token.provisioning_revision
        existing = AdoptionDeviceState.objects.filter(
            token=token, mac_address=mac_address
        ).first()
        applied_revision = existing.applied_revision if existing else 0
        needs_adoption = current_revision > applied_revision
        status = (
            AdoptionDeviceState.STATUS_NEEDS_PROVISIONING
            if needs_adoption
            else AdoptionDeviceState.STATUS_PROVISIONED
        )

        # Bookkeeping only: refresh last_seen_at and status. We never
        # touch applied_revision here — that is owned by the adopt path —
        # so a check can never make a router look "up to date".
        AdoptionDeviceState.objects.update_or_create(
            token=token,
            mac_address=mac_address,
            defaults={"status": status, "last_seen_at": timezone.now()},
        )

        logger.info(
            "provision.check: token=%s org=%s mac=%s current=%s applied=%s "
            "needs=%s",
            token.pk,
            token.organization_id,
            mac_address,
            current_revision,
            applied_revision,
            int(needs_adoption),
        )
        return JsonResponse(
            {
                "provisioning": {
                    "current_revision": current_revision,
                    "applied_revision": applied_revision,
                    "needs_adoption": needs_adoption,
                    "status": status,
                }
            },
            status=200,
        )


adopt = AdoptView.as_view()
check = CheckView.as_view()
