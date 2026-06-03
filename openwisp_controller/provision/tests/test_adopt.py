import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from swapper import load_model

from openwisp_users.tests.utils import TestOrganizationMixin

from ..models import AdoptionToken

Device = load_model("config", "Device")
OrganizationConfigSettings = load_model("config", "OrganizationConfigSettings")

ADOPT_URL = reverse("provision:adopt")
TEST_MAC = "AA:BB:CC:DD:EE:01"
TEST_SHARED_SECRET = "test-org-shared-secret"


class TestAdoptView(TestOrganizationMixin, TestCase):
    """Adoption endpoint contract tests."""

    def _create_org_with_settings(self, **org_kwargs):
        org = self._create_org(**org_kwargs)
        OrganizationConfigSettings.objects.create(
            organization=org, shared_secret=TEST_SHARED_SECRET
        )
        return org

    def _create_token(self, **kwargs):
        org = kwargs.pop("organization", None) or self._create_org_with_settings()
        defaults = dict(
            organization=org,
            description="test-token",
            radius_server="radius.example.com",
            radius_secret="r4d_secret",
            uam_server="https://login.wifi.lullex.com/login",
        )
        defaults.update(kwargs)
        return AdoptionToken.objects.create(**defaults)

    def _post(self, body):
        return self.client.post(
            ADOPT_URL, data=json.dumps(body), content_type="application/json"
        )

    def _payload(self, token):
        return {
            "token": token.token,
            "mac_address": TEST_MAC,
            "hostname": "lullex-router-1",
            "model": "Lullex AP v1",
            "agent_version": "1.0.0",
        }

    # ---------- happy path ----------
    def test_adopt_success_full_chilli(self):
        token = self._create_token()
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("openwisp", data)
        self.assertIn("chilli", data)
        self.assertTrue(data["openwisp"]["url"].endswith("/"))
        self.assertEqual(data["openwisp"]["shared_secret"], TEST_SHARED_SECRET)
        chilli = data["chilli"]
        self.assertEqual(chilli["radiusserver1"], "radius.example.com")
        self.assertEqual(chilli["radiussecret"], "r4d_secret")
        self.assertEqual(chilli["uamserver"], "https://login.wifi.lullex.com/login")
        self.assertEqual(chilli["uamallowed"], ["login.wifi.lullex.com"])
        self.assertEqual(chilli["net"], "192.168.182.0/24")
        self.assertEqual(chilli["uamlisten"], "192.168.182.1")
        self.assertEqual(chilli["uamport"], "3990")
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        self.assertEqual(token.last_used_mac, TEST_MAC)
        self.assertIsNotNone(token.last_used_at)

    def test_adopt_creates_device(self):
        token = self._create_token()
        self.assertFalse(Device.objects.filter(mac_address=TEST_MAC).exists())
        self._post(self._payload(token))
        device = Device.objects.get(mac_address=TEST_MAC)
        self.assertEqual(device.organization_id, token.organization_id)
        self.assertEqual(device.model, "Lullex AP v1")

    def test_adopt_uses_normalised_mac(self):
        token = self._create_token()
        body = self._payload(token)
        body["mac_address"] = TEST_MAC.lower()
        response = self._post(body)
        self.assertEqual(response.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.last_used_mac, TEST_MAC)

    # ---------- chilli partial / disabled ----------
    def test_adopt_omits_chilli_secrets_when_incomplete(self):
        token = self._create_token(radius_secret="")
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 200)
        chilli = response.json()["chilli"]
        self.assertNotIn("radiusserver1", chilli)
        self.assertNotIn("radiussecret", chilli)
        self.assertNotIn("uamserver", chilli)
        self.assertEqual(chilli["net"], "192.168.182.0/24")

    # ---------- error paths ----------
    def test_adopt_missing_token(self):
        body = {"mac_address": TEST_MAC}
        response = self._post(body)
        self.assertEqual(response.status_code, 400)

    def test_adopt_missing_mac(self):
        token = self._create_token()
        body = {"token": token.token}
        response = self._post(body)
        self.assertEqual(response.status_code, 400)

    def test_adopt_invalid_mac(self):
        token = self._create_token()
        body = self._payload(token)
        body["mac_address"] = "not-a-mac"
        response = self._post(body)
        self.assertEqual(response.status_code, 400)

    def test_adopt_unknown_token(self):
        self._create_token()  # ensure org settings exist
        body = {"token": "deadbeef" * 4, "mac_address": TEST_MAC}
        response = self._post(body)
        self.assertEqual(response.status_code, 403)

    def test_adopt_inactive_token(self):
        token = self._create_token(is_active=False)
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 403)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 0)

    def test_adopt_expired_token(self):
        token = self._create_token(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 403)

    def test_adopt_max_uses_reached(self):
        token = self._create_token(max_uses=1, use_count=1)
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 403)

    def test_adopt_inactive_org(self):
        org = self._create_org_with_settings()
        token = self._create_token(organization=org)
        org.is_active = False
        org.save()
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 403)

    def test_adopt_malformed_json(self):
        response = self.client.post(
            ADOPT_URL, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_adopt_get_not_allowed(self):
        response = self.client.get(ADOPT_URL)
        self.assertEqual(response.status_code, 405)

    def test_adopt_existing_device_other_org_not_overwritten(self):
        org1 = self._create_org_with_settings(name="org-one", slug="org-one")
        org2 = self._create_org_with_settings(name="org-two", slug="org-two")
        Device.objects.create(
            name="existing", organization=org1, mac_address=TEST_MAC
        )
        token = self._create_token(organization=org2)
        response = self._post(self._payload(token))
        # adoption still succeeds (router still gets openwisp config),
        # but the existing device is not moved between orgs
        self.assertEqual(response.status_code, 200)
        existing = Device.objects.get(mac_address=TEST_MAC)
        self.assertEqual(existing.organization_id, org1.id)


class TestSecretsNotLogged(TestOrganizationMixin, TestCase):
    """Token, shared_secret, radius_secret, and response body
    must never appear in log output.
    """

    def test_secrets_not_in_logs(self):
        org = self._create_org()
        OrganizationConfigSettings.objects.create(
            organization=org, shared_secret=TEST_SHARED_SECRET
        )
        token = AdoptionToken.objects.create(
            organization=org,
            description="logtest",
            radius_server="radius.example.com",
            radius_secret="super-secret-radius",
            uam_server="https://login.wifi.lullex.com/login",
        )
        body = json.dumps(
            {
                "token": token.token,
                "mac_address": TEST_MAC,
                "hostname": "h",
                "model": "m",
                "agent_version": "v",
            }
        )
        with self.assertLogs(
            "openwisp_controller.provision.views", level="INFO"
        ) as cap:
            response = self.client.post(
                ADOPT_URL, data=body, content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        joined = "\n".join(cap.output)
        self.assertNotIn(token.token, joined)
        self.assertNotIn(TEST_SHARED_SECRET, joined)
        self.assertNotIn("super-secret-radius", joined)
        self.assertNotIn("radiussecret", joined)
