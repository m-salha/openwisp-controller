import logging

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import AdoptedDevice, AdoptionToken, OrganizationProvisioningConfig
from .serializers import AdoptionRequestSerializer
from .throttling import AdoptionRateThrottle

logger = logging.getLogger(__name__)

# Intentionally vague — do not reveal why a token is invalid.
_INVALID = {'detail': 'Invalid or expired token.'}


class AdoptView(APIView):
    """
    POST /api/provision/adopt/

    Public endpoint (no auth required).  A device submits its adoption token
    and hardware identity; if the token is valid the response contains all
    config the device needs to self-register with the OpenWISP controller and
    optionally bring up WireGuard + RADIUS + Captive Portal.
    """

    authentication_classes = []
    permission_classes = []
    throttle_classes = [AdoptionRateThrottle]

    def post(self, request):
        serializer = AdoptionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # ── 1. Look up token ─────────────────────────────────────────────────
        try:
            token = AdoptionToken.objects.select_related('organization').get(
                token=data['token']
            )
        except AdoptionToken.DoesNotExist:
            return Response(_INVALID, status=status.HTTP_401_UNAUTHORIZED)

        valid, _ = token.validate()
        if not valid:
            return Response(_INVALID, status=status.HTTP_401_UNAUTHORIZED)

        org = token.organization

        # ── 2. Load provisioning config ──────────────────────────────────────
        try:
            prov = org.provisioning_config
        except OrganizationProvisioningConfig.DoesNotExist:
            logger.error('No provisioning config for org %s', org.pk)
            return Response(
                {'detail': 'Provisioning not configured for this organisation.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not prov.enabled:
            return Response(
                {'detail': 'Provisioning is disabled for this organisation.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # ── 3. Retrieve shared_secret from OpenWISP org config settings ──────
        shared_secret = None
        try:
            shared_secret = org.config_settings.shared_secret
        except Exception:
            logger.warning(
                'Could not retrieve shared_secret for org %s — '
                'OrganizationConfigSettings may not exist.',
                org.pk,
            )

        # ── 4. Persist adoption record + consume token (atomic) ───────────────
        with transaction.atomic():
            wg_ip = None
            if data['public_wireguard_key'] and prov.wireguard_server_public_key:
                wg_ip = prov.allocate_wireguard_ip()

            AdoptedDevice.objects.create(
                token=token,
                mac_address=data['mac_address'],
                hostname=data['hostname'],
                model=data['model'],
                wireguard_public_key=data['public_wireguard_key'],
                wireguard_assigned_ip=wg_ip,
            )
            token.consume()

        # ── 5. Build response ────────────────────────────────────────────────
        response_data = {
            'controller_url': prov.controller_url,
            'organization': org.name,
            'organization_uuid': str(org.pk),
            'mode': prov.mode,
        }

        if shared_secret:
            response_data['shared_secret'] = shared_secret

        if prov.wireguard_server_public_key:
            wg = {
                'enabled': True,
                'server_public_key': prov.wireguard_server_public_key,
                'endpoint': prov.wireguard_server_endpoint,
                'allowed_ips': prov.wireguard_allowed_ips,
            }
            if wg_ip:
                wg['address'] = f'{wg_ip}/32'
            if prov.wireguard_dns:
                wg['dns'] = prov.wireguard_dns
            response_data['wireguard'] = wg

        if prov.radius_server_ip:
            response_data['radius'] = {
                'server': prov.radius_server_ip,
                'auth_port': prov.radius_server_port,
                'acct_port': prov.radius_acct_port,
                'secret': prov.radius_secret,
            }

        if prov.captive_portal_enabled:
            # papalwaysok + nochallenge are always set.
            # chap=0, uamallowed=0.0.0.0/0, mschapv2 are intentionally omitted.
            cp = {
                'enabled': True,
                'uamserver': prov.captive_portal_uam_server,
                'uamport': prov.captive_portal_uam_port,
                'dhcpif': prov.captive_portal_dhcpif,
                'tundev': prov.captive_portal_tundev,
                'papalwaysok': True,
                'nochallenge': True,
            }
            if prov.captive_portal_nas_id:
                cp['nasid'] = prov.captive_portal_nas_id
            if prov.captive_portal_uam_secret:
                cp['uamsecret'] = prov.captive_portal_uam_secret
            response_data['captive_portal'] = cp

        logger.info(
            'Device adopted: mac=%s hostname=%s org=%s mode=%s',
            data['mac_address'],
            data['hostname'],
            org.name,
            prov.mode,
        )

        return Response(response_data, status=status.HTTP_200_OK)
