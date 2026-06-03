import json
import logging
import re

from django.db import transaction
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
    POST /api/provision/adopt/

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

        try:
            token = AdoptionToken.objects.select_related("organization").get(
                token=token_value
            )
        except AdoptionToken.DoesNotExist:
            logger.warning(
                "provision.adopt: unknown token (mac=%s)", mac_address
            )
            return _json_error("invalid token", status=403)

        ok, reason = token.is_usable()
        if not ok:
            logger.warning(
                "provision.adopt: token %s not usable (%s, mac=%s)",
                token.pk,
                reason,
                mac_address,
            )
            return _json_error("invalid token", status=403)

        organization = token.organization
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

        self._register_device(token, organization, mac_address, payload)
        self._mark_used(token, mac_address)

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
            "provision.adopt: success token=%s org=%s mac=%s",
            token.pk,
            organization.pk,
            mac_address,
        )
        return JsonResponse(response_body, status=200)

    def _register_device(self, token, organization, mac_address, payload):
        """
        Best-effort: ensure a Device row exists for this MAC inside the
        token's organization. Refuses if the MAC is already claimed by a
        different organization. Failures here do not block the adoption
        response (the router can still self-register via the existing
        OpenWISP controller/register/ endpoint).
        """
        try:
            existing = Device.objects.filter(mac_address=mac_address).first()
            if existing and existing.organization_id != organization.id:
                logger.warning(
                    "provision.adopt: mac %s already owned by org %s",
                    mac_address,
                    existing.organization_id,
                )
                return
            if existing:
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

    @staticmethod
    def _mark_used(token, mac_address):
        with transaction.atomic():
            AdoptionToken.objects.filter(pk=token.pk).update(
                use_count=token.use_count + 1,
                last_used_at=timezone.now(),
                last_used_mac=mac_address,
            )


adopt = AdoptView.as_view()
