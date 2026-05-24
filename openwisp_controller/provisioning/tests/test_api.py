import swapper
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import AdoptedDevice, AdoptionToken, OrganizationProvisioningConfig

Organization = swapper.load_model('openwisp_users', 'Organization')

ADOPT_URL = '/api/provision/adopt/'

_VALID_PAYLOAD = {
    'token': '',           # filled in per-test
    'mac_address': '08:00:27:6A:D1:87',
    'hostname': 'openwrt-test',
    'model': 'x86_64-vbox',
}


def _make_org(name='Lullex', slug='lullex'):
    return Organization.objects.create(name=name, slug=slug)


def _make_token(org, **kwargs):
    return AdoptionToken.objects.create(organization=org, **kwargs)


def _make_prov(org, **kwargs):
    defaults = dict(
        controller_url='https://controller.wifi.lullex.com',
        mode=OrganizationProvisioningConfig.MODE_OPENWISP,
    )
    defaults.update(kwargs)
    return OrganizationProvisioningConfig.objects.create(organization=org, **defaults)


class AdoptViewBasicTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.token = _make_token(self.org)
        self.prov = _make_prov(self.org)

    def _post(self, payload=None):
        data = {**_VALID_PAYLOAD, 'token': self.token.token}
        if payload:
            data.update(payload)
        return self.client.post(ADOPT_URL, data, format='json')

    def test_valid_token_returns_200(self):
        # config_settings may not exist in the test DB; view handles that gracefully.
        response = self._post()
        self.assertEqual(response.status_code, 200)

    def test_response_contains_controller_url(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data['controller_url'],
            'https://controller.wifi.lullex.com',
        )

    def test_response_contains_organization(self):
        response = self._post()
        self.assertEqual(response.data['organization'], 'Lullex')

    def test_response_contains_mode(self):
        response = self._post()
        self.assertEqual(response.data['mode'], OrganizationProvisioningConfig.MODE_OPENWISP)

    def test_adopted_device_record_created(self):
        self._post()
        self.assertEqual(AdoptedDevice.objects.filter(mac_address='08:00:27:6A:D1:87').count(), 1)

    def test_uses_count_incremented(self):
        self._post()
        self.token.refresh_from_db()
        self.assertEqual(self.token.uses_count, 1)

    def test_one_time_token_marked_used_after_adoption(self):
        self.token.max_uses = 1
        self.token.save()
        self._post()
        self.token.refresh_from_db()
        self.assertEqual(self.token.status, AdoptionToken.USED)


class AdoptViewInvalidTokenTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.prov = _make_prov(self.org)

    def _post(self, token_str):
        return self.client.post(
            ADOPT_URL,
            {**_VALID_PAYLOAD, 'token': token_str},
            format='json',
        )

    def test_nonexistent_token_returns_401(self):
        response = self._post('DOES-NOT-EXIST')
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_returns_401(self):
        token = _make_token(self.org, status=AdoptionToken.REVOKED)
        response = self._post(token.token)
        self.assertEqual(response.status_code, 401)

    def test_used_token_returns_401(self):
        token = _make_token(self.org, status=AdoptionToken.USED)
        response = self._post(token.token)
        self.assertEqual(response.status_code, 401)

    def test_error_message_is_vague(self):
        """Error must not distinguish 'not found' from 'revoked'."""
        response = self._post('RANDOM-TOKEN-XYZ')
        self.assertEqual(response.data['detail'], 'Invalid or expired token.')


class AdoptViewValidationTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.token = _make_token(self.org)
        self.prov = _make_prov(self.org)

    def test_invalid_mac_returns_400(self):
        response = self.client.post(
            ADOPT_URL,
            {'token': self.token.token, 'mac_address': 'not-a-mac'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('mac_address', response.data)

    def test_missing_token_returns_400(self):
        response = self.client.post(
            ADOPT_URL,
            {'mac_address': '08:00:27:6A:D1:87'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_wireguard_key_returns_400(self):
        response = self.client.post(
            ADOPT_URL,
            {
                'token': self.token.token,
                'mac_address': '08:00:27:6A:D1:87',
                'public_wireguard_key': 'not-a-valid-key',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('public_wireguard_key', response.data)

    def test_mac_normalised_to_uppercase(self):
        self.client.post(
            ADOPT_URL,
            {'token': self.token.token, 'mac_address': '08:00:27:6a:d1:87'},
            format='json',
        )
        device = AdoptedDevice.objects.first()
        self.assertEqual(device.mac_address, '08:00:27:6A:D1:87')


class AdoptViewNoProvisoningConfigTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.token = _make_token(self.org)

    def test_returns_503_when_no_provisioning_config(self):
        response = self.client.post(
            ADOPT_URL,
            {**_VALID_PAYLOAD, 'token': self.token.token},
            format='json',
        )
        self.assertEqual(response.status_code, 503)

    def test_returns_503_when_provisioning_disabled(self):
        _make_prov(self.org, enabled=False)
        response = self.client.post(
            ADOPT_URL,
            {**_VALID_PAYLOAD, 'token': self.token.token},
            format='json',
        )
        self.assertEqual(response.status_code, 503)


class AdoptViewWireGuardTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.token = _make_token(self.org)
        self.prov = _make_prov(
            self.org,
            mode=OrganizationProvisioningConfig.MODE_VPN_RADIUS,
            wireguard_server_public_key='dGVzdHB1YmxpY2tleXZhbHVlZm9ydGVzdGluZzEyMzQ=',
            wireguard_server_endpoint='vpn.lullex.com:51820',
            wireguard_address_pool='10.100.10.0/24',
            wireguard_allowed_ips='10.100.0.0/16',
            radius_server_ip='10.100.0.1',
            radius_secret='radius-secret',
        )

    def _post(self, wg_key='dGVzdHB1YmxpY2tleXZhbHVlZm9ydGVzdGluZzEyMzQ='):
        return self.client.post(
            ADOPT_URL,
            {**_VALID_PAYLOAD, 'token': self.token.token, 'public_wireguard_key': wg_key},
            format='json',
        )

    def test_wireguard_block_present_when_configured(self):
        response = self._post()
        self.assertIn('wireguard', response.data)

    def test_wireguard_address_allocated(self):
        response = self._post()
        self.assertIn('address', response.data['wireguard'])
        # Should be within the pool
        self.assertTrue(response.data['wireguard']['address'].startswith('10.100.10.'))

    def test_radius_block_present(self):
        response = self._post()
        self.assertIn('radius', response.data)
        self.assertEqual(response.data['radius']['server'], '10.100.0.1')

    def test_no_wireguard_block_without_server_key(self):
        self.prov.wireguard_server_public_key = ''
        self.prov.save()
        response = self._post()
        self.assertNotIn('wireguard', response.data)


class AdoptViewCaptivePortalTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = _make_org()
        self.token = _make_token(self.org)
        self.prov = _make_prov(
            self.org,
            captive_portal_enabled=True,
            captive_portal_uam_server='https://login.wifi.lullex.com/lullex/login',
            captive_portal_dhcpif='br-lan',
            captive_portal_tundev='tun1',
        )

    def _post(self):
        return self.client.post(
            ADOPT_URL,
            {**_VALID_PAYLOAD, 'token': self.token.token},
            format='json',
        )

    def test_captive_portal_block_present(self):
        response = self._post()
        self.assertIn('captive_portal', response.data)

    def test_papalwaysok_is_true(self):
        response = self._post()
        self.assertTrue(response.data['captive_portal']['papalwaysok'])

    def test_nochallenge_is_true(self):
        response = self._post()
        self.assertTrue(response.data['captive_portal']['nochallenge'])

    def test_dhcpif_and_tundev_set(self):
        cp = self._post().data['captive_portal']
        self.assertEqual(cp['dhcpif'], 'br-lan')
        self.assertEqual(cp['tundev'], 'tun1')

    def test_chap_not_in_response(self):
        cp = self._post().data['captive_portal']
        self.assertNotIn('chap', cp)

    def test_uamallowed_not_in_response(self):
        cp = self._post().data['captive_portal']
        self.assertNotIn('uamallowed', cp)

    def test_mschapv2_not_in_response(self):
        cp = self._post().data['captive_portal']
        self.assertNotIn('mschapv2', cp)

    def test_captive_portal_absent_when_disabled(self):
        self.prov.captive_portal_enabled = False
        self.prov.save()
        response = self._post()
        self.assertNotIn('captive_portal', response.data)
