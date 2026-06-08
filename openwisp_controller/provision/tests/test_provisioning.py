import json
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from swapper import load_model

from openwisp_users.tests.utils import TestOrganizationMixin

from ..models import AdoptionDeviceState, AdoptionToken

Device = load_model("config", "Device")
OrganizationConfigSettings = load_model("config", "OrganizationConfigSettings")

ADOPT_URL = reverse("provision:adopt")
CHECK_URL = reverse("provision:check")
TEST_MAC = "AA:BB:CC:DD:EE:01"


class ProvisionTestMixin(TestOrganizationMixin):
    """Helpers shared with the adoption tests, kept self-contained."""

    def _create_org_with_settings(self, shared_secret=None, **org_kwargs):
        org = self._create_org(**org_kwargs)
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

    def _post(self, url, token, **overrides):
        body = {
            "token": token.token,
            "mac_address": TEST_MAC,
            "hostname": "lullex-router-1",
            "model": "Lullex AP v1",
            "agent_version": "1.0.0",
        }
        body.update(overrides)
        return self.client.post(
            url, data=json.dumps(body), content_type="application/json"
        )


class TestProvisioningRevision(ProvisionTestMixin, TestCase):
    """Revision tracking on AdoptionToken."""

    def test_new_token_starts_at_revision_one(self):
        token = self._create_token()
        self.assertEqual(token.provisioning_revision, 1)
        self.assertIsNone(token.revision_updated_at)

    def test_changing_radius_field_increments_revision(self):
        token = self._create_token()
        token.radius_server = "radius2.example.com"
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 2)
        self.assertIsNotNone(token.revision_updated_at)

    def test_changing_uam_allowed_increments_revision(self):
        token = self._create_token()
        token.uam_allowed = ["login.wifi.lullex.com", "extra.example.com"]
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 2)

    def test_changing_chilli_net_increments_revision(self):
        token = self._create_token()
        token.chilli_net = "10.10.0.0/24"
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 2)

    def test_changing_non_provisioning_field_does_not_increment(self):
        token = self._create_token()
        token.description = "renamed token"
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 1)

    def test_repeated_changes_increment_each_time(self):
        token = self._create_token()
        token.radius_server = "r2.example.com"
        token.save()
        token.radius_secret = "another-secret"
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 3)


class TestAdoptProvisioningState(ProvisionTestMixin, TestCase):
    """The adopt endpoint records per-router provisioning state."""

    def test_adopt_returns_provisioning_block(self):
        token = self._create_token()
        response = self._post(ADOPT_URL, token)
        self.assertEqual(response.status_code, 200)
        prov = response.json()["provisioning"]
        self.assertEqual(prov["revision"], token.provisioning_revision)
        self.assertEqual(prov["status"], "provisioned")

    def test_adopt_creates_device_state_with_applied_revision(self):
        token = self._create_token()
        response = self._post(ADOPT_URL, token)
        self.assertEqual(response.status_code, 200)
        state = AdoptionDeviceState.objects.get(
            token=token, mac_address=TEST_MAC
        )
        self.assertEqual(state.applied_revision, token.provisioning_revision)
        self.assertEqual(state.status, AdoptionDeviceState.STATUS_PROVISIONED)
        self.assertIsNotNone(state.last_seen_at)
        self.assertIsNotNone(state.last_adopted_at)

    def test_readoption_updates_applied_revision_after_change(self):
        """First adoption with a fresh MAC consumes exactly one
        use_count slot; after a revision bump, re-adoption by the same
        MAC carries the new applied_revision but does NOT consume
        another slot.

        Setup invariant: the MAC used here must not already have a
        Device row, otherwise the first POST would itself take the
        idempotent path. We use a MAC distinct from TEST_MAC (which is
        exercised by other tests) and assert the precondition.
        """
        token = self._create_token()
        initial_use_count = token.use_count
        mac = "AA:BB:CC:DD:EE:7B"
        # Precondition: no Device exists for this MAC yet, so the first
        # POST goes down the new-adoption path and consumes one slot.
        self.assertFalse(Device.objects.filter(mac_address=mac).exists())

        first = self._post(ADOPT_URL, token, mac_address=mac)
        self.assertEqual(first.status_code, 200)
        # Checkpoint: confirm the first POST really was a new adoption
        # (use_count grew by exactly one, Device row was created).
        token.refresh_from_db()
        self.assertEqual(token.use_count, initial_use_count + 1)
        self.assertTrue(Device.objects.filter(mac_address=mac).exists())

        state = AdoptionDeviceState.objects.get(
            token=token, mac_address=mac
        )
        self.assertEqual(state.applied_revision, 1)

        # Admin changes a RADIUS field -> revision bumps to 2.
        token.radius_server = "radius2.example.com"
        token.save()
        token.refresh_from_db()
        self.assertEqual(token.provisioning_revision, 2)

        # Same MAC re-adopts: the Device created above drives the
        # idempotent path, so applied_revision catches up but use_count
        # must NOT increase.
        second = self._post(ADOPT_URL, token, mac_address=mac)
        self.assertEqual(second.status_code, 200)
        state.refresh_from_db()
        self.assertEqual(state.applied_revision, 2)
        token.refresh_from_db()
        self.assertEqual(token.use_count, initial_use_count + 1)

    def test_adopt_idempotency_preserved_with_state(self):
        token = self._create_token(max_uses=5)
        self._post(ADOPT_URL, token)
        self._post(ADOPT_URL, token)
        self._post(ADOPT_URL, token)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        self.assertEqual(
            AdoptionDeviceState.objects.filter(
                token=token, mac_address=TEST_MAC
            ).count(),
            1,
        )


class TestCheckView(ProvisionTestMixin, TestCase):
    """The lightweight check endpoint."""

    def test_check_unknown_state_needs_adoption(self):
        token = self._create_token()
        response = self.client.post(
            CHECK_URL,
            data=json.dumps(
                {"token": token.token, "mac_address": TEST_MAC}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        prov = response.json()["provisioning"]
        self.assertEqual(prov["current_revision"], 1)
        self.assertEqual(prov["applied_revision"], 0)
        self.assertTrue(prov["needs_adoption"])
        self.assertEqual(prov["status"], "needs_provisioning")

    def test_check_after_adoption_does_not_need_adoption(self):
        token = self._create_token()
        self._post(ADOPT_URL, token)
        response = self._post(CHECK_URL, token)
        self.assertEqual(response.status_code, 200)
        prov = response.json()["provisioning"]
        self.assertEqual(prov["current_revision"], 1)
        self.assertEqual(prov["applied_revision"], 1)
        self.assertFalse(prov["needs_adoption"])
        self.assertEqual(prov["status"], "provisioned")

    def test_check_needs_adoption_after_revision_change(self):
        token = self._create_token()
        self._post(ADOPT_URL, token)
        # Admin changes a UAM/RADIUS field -> revision bumps.
        token.uam_server = "https://login2.wifi.lullex.com/login"
        token.save()
        token.refresh_from_db()
        response = self._post(CHECK_URL, token)
        self.assertEqual(response.status_code, 200)
        prov = response.json()["provisioning"]
        self.assertEqual(prov["current_revision"], 2)
        self.assertEqual(prov["applied_revision"], 1)
        self.assertTrue(prov["needs_adoption"])
        self.assertEqual(prov["status"], "needs_provisioning")

    def test_check_does_not_increment_use_count(self):
        token = self._create_token(max_uses=5)
        self._post(ADOPT_URL, token)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)
        for _ in range(3):
            self._post(CHECK_URL, token)
        token.refresh_from_db()
        self.assertEqual(token.use_count, 1)

    def test_check_does_not_create_device(self):
        token = self._create_token()
        self._post(CHECK_URL, token, mac_address="AA:BB:CC:DD:EE:99")
        self.assertFalse(
            Device.objects.filter(mac_address="AA:BB:CC:DD:EE:99").exists()
        )

    def test_check_updates_last_seen(self):
        token = self._create_token()
        self._post(CHECK_URL, token)
        state = AdoptionDeviceState.objects.get(
            token=token, mac_address=TEST_MAC
        )
        self.assertIsNotNone(state.last_seen_at)
        # A check must never set last_adopted_at.
        self.assertIsNone(state.last_adopted_at)

    def test_check_response_contains_no_secrets(self):
        token = self._create_token(
            radius_secret="super-secret-radius",
        )
        # Adopt first so a state row with the secret-bearing token exists.
        self._post(ADOPT_URL, token)
        response = self._post(CHECK_URL, token)
        self.assertEqual(response.status_code, 200)
        raw = response.content.decode("utf-8")
        org_secret = token.organization.config_settings.shared_secret
        self.assertNotIn(token.token, raw)
        self.assertNotIn("super-secret-radius", raw)
        self.assertNotIn("radiussecret", raw)
        self.assertNotIn("radiusserver1", raw)
        self.assertNotIn("uamserver", raw)
        self.assertNotIn("shared_secret", raw)
        self.assertNotIn(org_secret, raw)
        # Only the provisioning envelope is returned.
        self.assertEqual(list(response.json().keys()), ["provisioning"])

    # ---------- validation / error paths ----------
    def test_check_missing_token(self):
        response = self.client.post(
            CHECK_URL,
            data=json.dumps({"mac_address": TEST_MAC}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_check_missing_mac(self):
        token = self._create_token()
        response = self.client.post(
            CHECK_URL,
            data=json.dumps({"token": token.token}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_check_invalid_mac(self):
        token = self._create_token()
        response = self._post(CHECK_URL, token, mac_address="not-a-mac")
        self.assertEqual(response.status_code, 400)

    def test_check_malformed_json(self):
        response = self.client.post(
            CHECK_URL, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_check_unknown_token(self):
        self._create_token()
        response = self.client.post(
            CHECK_URL,
            data=json.dumps(
                {"token": "deadbeef" * 4, "mac_address": TEST_MAC}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_check_inactive_token(self):
        token = self._create_token(is_active=False)
        response = self._post(CHECK_URL, token)
        self.assertEqual(response.status_code, 403)

    def test_check_get_not_allowed(self):
        response = self.client.get(CHECK_URL)
        self.assertEqual(response.status_code, 405)


class TestCheckSecretsNotLogged(ProvisionTestMixin, TestCase):
    def test_secrets_not_in_logs(self):
        token = self._create_token(radius_secret="super-secret-radius")
        org_secret = token.organization.config_settings.shared_secret
        with self.assertLogs(
            "openwisp_controller.provision.views", level="INFO"
        ) as cap:
            response = self._post(CHECK_URL, token)
        self.assertEqual(response.status_code, 200)
        joined = "\n".join(cap.output)
        self.assertNotIn(token.token, joined)
        self.assertNotIn("super-secret-radius", joined)
        self.assertNotIn(org_secret, joined)
