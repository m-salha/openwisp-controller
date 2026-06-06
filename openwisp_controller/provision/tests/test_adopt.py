import json
from datetime import timedelta
from uuid import uuid4

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

    def _create_org_with_settings(self, shared_secret=None, **org_kwargs):
        org = self._create_org(**org_kwargs)
        # shared_secret is unique; generate a distinct value per org so
        # tests that create more than one organization do not collide.
        OrganizationConfigSettings.objects.create(
            organization=org, shared_secret=shared_secret or uuid4().hex
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

    def _payload(self, token, **overrides):
        data = {
            "token": token.token,
            "mac_address": TEST_MAC,
            "hostname": "lullex-router-1",
            "model": "Lullex AP v1",
            "agent_version": "1.0.0",
        }
        data.update(overrides)
        return data

    # ---------- happy path ----------
    def test_adopt_success_full_chilli(self):
        token = self._create_token()
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("openwisp", data)
        self.assertIn("chilli", data)
        self.assertTrue(data["openwisp"]["url"].endswith("/"))
        expected_secret = token.organization.config_settings.shared_secret
        self.assertEqual(data["openwisp"]["shared_secret"], expected_secret)
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

    def test_adopt_rejects_when_mac_owned_by_other_org(self):
        """A MAC already claimed by another organization must yield 403,
        must NOT leak the token org's shared_secret, must leave the
        existing device untouched, and must NOT consume a token slot."""
        org1 = self._create_org_with_settings(name="org-one", slug="org-one")
        org2 = self._create_org_with_settings(name="org-two", slug="org-two")
        Device.objects.create(
            name="existing", organization=org1, mac_address=TEST_MAC
        )
        token = self._create_token(organization=org2)
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertNotIn("openwisp", body)
        self.assertNotIn("chilli", body)
        # Defensive: org2's shared_secret must not appear in the body.
        org2_secret = org2.config_settings.shared_secret
        self.assertNotIn(org2_secret, json.dumps(body))
        # The existing device must not be moved between organizations.
        existing = Device.objects.get(mac_address=TEST_MAC)
        self.assertEqual(existing.organization_id, org1.id)
        # The token must not be consumed.
        token.refresh_from_db()
        self.assertEqual(token.use_count, 0)
        self.assertIsNone(token.last_used_at)

    def test_adopt_max_uses_strict_under_sequential_requests(self):
        """max_uses counts unique MACs. Adopting three DISTINCT MACs
        against a max_uses=2 token must allow the first two and reject
        the third; use_count must end at exactly 2."""
        token = self._create_token(max_uses=2)
        r1 = self._post(self._payload(token, mac_address="AA:BB:CC:DD:EE:01"))
        r2 = self._post(self._payload(token, mac_address="AA:BB:CC:DD:EE:02"))
        r3 = self._post(self._payload(token, mac_address="AA:BB:CC:DD:EE:03"))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r3.status_code, 403)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 2)

    # ---------- idempotent re-adoption ----------
    def test_adopt_first_time_consumes_use_count(self):
        """The first adoption of a MAC consumes exactly one slot."""
        token = self._create_token(max_uses=5)
        self.assertEqual(token.use_count, 0)
        response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)

    def test_adopt_same_mac_reuse_does_not_consume_slot(self):
        """Re-adoption by the same MAC must succeed without consuming
        another use_count slot, no matter how many times it repeats."""
        token = self._create_token(max_uses=5)
        first = self._post(self._payload(token))
        self.assertEqual(first.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        for _ in range(3):
            again = self._post(self._payload(token))
            self.assertEqual(again.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        # Exactly one Device must exist for the MAC.
        self.assertEqual(
            Device.objects.filter(mac_address=TEST_MAC).count(), 1
        )

    def test_adopt_same_mac_reuse_returns_updated_chilli(self):
        """Re-adoption must return the latest token values. A token that
        starts with Chilli disabled and is later given RADIUS/UAM values
        must hand the new chilli block to the re-adopting router, still
        without consuming a second slot."""
        token = self._create_token(
            max_uses=5, radius_server="", radius_secret="", uam_server=""
        )
        first = self._post(self._payload(token))
        self.assertEqual(first.status_code, 200)
        chilli_before = first.json()["chilli"]
        self.assertNotIn("radiusserver1", chilli_before)
        self.assertNotIn("radiussecret", chilli_before)
        self.assertNotIn("uamserver", chilli_before)
        # Admin enables Chilli on the token (GUI change).
        token.radius_server = "radius.example.com"
        token.radius_secret = "r4d_secret"
        token.uam_server = "https://login.wifi.lullex.com/login"
        token.chilli_net = "10.10.0.0/24"
        token.save()
        # Same MAC re-adopts and must receive the new values.
        second = self._post(self._payload(token))
        self.assertEqual(second.status_code, 200)
        chilli_after = second.json()["chilli"]
        self.assertEqual(chilli_after["radiusserver1"], "radius.example.com")
        self.assertEqual(chilli_after["radiussecret"], "r4d_secret")
        self.assertEqual(
            chilli_after["uamserver"], "https://login.wifi.lullex.com/login"
        )
        self.assertEqual(chilli_after["net"], "10.10.0.0/24")
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)

    def test_adopt_new_mac_blocked_when_max_uses_reached(self):
        """Once max_uses is reached a NEW MAC is rejected, but the
        already-adopted MAC may keep re-adopting."""
        token = self._create_token(max_uses=1)
        adopted = self._post(
            self._payload(token, mac_address="AA:BB:CC:DD:EE:01")
        )
        self.assertEqual(adopted.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        # A different/new MAC must be blocked now.
        blocked = self._post(
            self._payload(token, mac_address="AA:BB:CC:DD:EE:02")
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertFalse(
            Device.objects.filter(mac_address="AA:BB:CC:DD:EE:02").exists()
        )
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        # The already-adopted MAC can still re-adopt.
        reused = self._post(
            self._payload(token, mac_address="AA:BB:CC:DD:EE:01")
        )
        self.assertEqual(reused.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)

    def test_adopt_max_uses_safe_under_simulated_race(self):
        """If a competing increment consumes the last slot between the
        is_usable() check and the conditional UPDATE, the view's UPDATE
        must match zero rows and the request must be rejected.

        The simulated competing bump runs on the same connection inside
        the view's atomic block, so it is rolled back together with that
        block; we therefore assert that use_count never exceeds max_uses
        rather than asserting an exact post-race value. The guarantees
        verified here are: HTTP 403, no openwisp block in the response,
        no Device row created, and use_count <= max_uses.
        """
        from unittest.mock import patch

        token = self._create_token(max_uses=1)
        real_filter = AdoptionToken.objects.filter

        def filter_with_concurrent_bump(*args, **kwargs):
            qs = real_filter(*args, **kwargs)
            # Only intercept the conditional update filter
            # (use_count__lt is only used by the increment call).
            if "use_count__lt" in kwargs:
                # Simulate a concurrent adoption that consumed the slot
                # after our is_usable() check but before our UPDATE.
                AdoptionToken.objects.filter(pk=token.pk).update(
                    use_count=token.max_uses
                )
            return qs

        with patch.object(
            AdoptionToken.objects,
            "filter",
            side_effect=filter_with_concurrent_bump,
        ):
            response = self._post(self._payload(token))

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertNotIn("openwisp", body)
        # No device row may be created on a lost race.
        self.assertFalse(
            Device.objects.filter(mac_address=TEST_MAC).exists()
        )
        # use_count must never exceed the cap.
        token.refresh_from_db()
        self.assertLessEqual(token.use_count, token.max_uses)

    def test_adopt_uses_select_for_update_on_token(self):
        """The token lookup inside the critical section must use
        select_for_update so that, on backends that honour it, the
        token row is locked while we validate and bump use_count."""
        from unittest.mock import patch

        token = self._create_token()
        manager_cls = type(AdoptionToken.objects)
        original = manager_cls.select_for_update
        with patch.object(
            manager_cls,
            "select_for_update",
            autospec=True,
            side_effect=lambda self, *a, **kw: original(self, *a, **kw),
        ) as mock_sfu:
            response = self._post(self._payload(token))
        self.assertEqual(response.status_code, 200)
        mock_sfu.assert_called()


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
            # First adoption (new MAC) and a re-adoption (same MAC) must
            # both keep secrets out of the logs.
            response = self.client.post(
                ADOPT_URL, data=body, content_type="application/json"
            )
            readopt = self.client.post(
                ADOPT_URL, data=body, content_type="application/json"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(readopt.status_code, 200)
        joined = "\n".join(cap.output)
        self.assertNotIn(token.token, joined)
        self.assertNotIn(TEST_SHARED_SECRET, joined)
        self.assertNotIn("super-secret-radius", joined)
        self.assertNotIn("radiussecret", joined)
