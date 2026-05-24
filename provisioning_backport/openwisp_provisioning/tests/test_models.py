from datetime import timedelta

import swapper
from django.test import TestCase
from django.utils import timezone

from ..models import AdoptedDevice, AdoptionToken, OrganizationProvisioningConfig

Organization = swapper.load_model("openwisp_users", "Organization")


def _make_org(name="Test Org", slug="test-org"):
    return Organization.objects.create(name=name, slug=slug)


def _make_token(org, **kwargs):
    return AdoptionToken.objects.create(organization=org, **kwargs)


def _make_prov_config(org, **kwargs):
    defaults = dict(
        controller_url="https://controller.example.com",
        wireguard_address_pool="10.100.10.0/28",
    )
    defaults.update(kwargs)
    return OrganizationProvisioningConfig.objects.create(organization=org, **defaults)


class TokenGenerationTest(TestCase):
    def test_token_auto_generated(self):
        token = _make_token(_make_org())
        self.assertTrue(len(token.token) >= 60)

    def test_tokens_are_unique(self):
        org = _make_org()
        self.assertNotEqual(_make_token(org).token, _make_token(org).token)

    def test_default_status_is_active(self):
        self.assertEqual(_make_token(_make_org()).status, AdoptionToken.ACTIVE)


class TokenValidationTest(TestCase):
    def setUp(self):
        self.org = _make_org()

    def test_active_token_is_valid(self):
        valid, err = _make_token(self.org).validate()
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_revoked_token_is_invalid(self):
        valid, err = _make_token(self.org, status=AdoptionToken.REVOKED).validate()
        self.assertFalse(valid)
        self.assertIn("revoked", err.lower())

    def test_used_token_is_invalid(self):
        valid, err = _make_token(self.org, status=AdoptionToken.USED).validate()
        self.assertFalse(valid)

    def test_expired_datetime_makes_token_invalid(self):
        token = _make_token(self.org, expires_at=timezone.now() - timedelta(seconds=1))
        valid, err = token.validate()
        self.assertFalse(valid)
        self.assertIn("expired", err.lower())
        token.refresh_from_db()
        self.assertEqual(token.status, AdoptionToken.EXPIRED)

    def test_future_expiry_still_valid(self):
        token = _make_token(self.org, expires_at=timezone.now() + timedelta(hours=1))
        valid, err = token.validate()
        self.assertTrue(valid)

    def test_max_uses_exceeded_is_invalid(self):
        token = _make_token(self.org, max_uses=2, uses_count=2)
        valid, err = token.validate()
        self.assertFalse(valid)
        token.refresh_from_db()
        self.assertEqual(token.status, AdoptionToken.USED)


class TokenConsumeTest(TestCase):
    def setUp(self):
        self.org = _make_org()

    def test_consume_increments_uses_count(self):
        token = _make_token(self.org)
        token.consume()
        self.assertEqual(token.uses_count, 1)

    def test_consume_marks_used_when_max_uses_reached(self):
        token = _make_token(self.org, max_uses=1)
        token.consume()
        token.refresh_from_db()
        self.assertEqual(token.status, AdoptionToken.USED)

    def test_consume_stays_active_below_max_uses(self):
        token = _make_token(self.org, max_uses=3)
        token.consume()
        token.refresh_from_db()
        self.assertEqual(token.status, AdoptionToken.ACTIVE)
        self.assertEqual(token.uses_count, 1)


class WireguardIpAllocationTest(TestCase):
    def setUp(self):
        self.org = _make_org()
        self.prov = _make_prov_config(self.org, wireguard_address_pool="10.0.0.0/30")

    def _adopted(self, ip):
        AdoptedDevice.objects.create(
            token=_make_token(self.org),
            mac_address="AA:BB:CC:DD:EE:FF",
            wireguard_assigned_ip=ip,
        )

    def test_first_ip_allocated(self):
        self.assertIn(self.prov.allocate_wireguard_ip(), ("10.0.0.1", "10.0.0.2"))

    def test_skips_used_ips(self):
        self._adopted("10.0.0.1")
        self.assertEqual(self.prov.allocate_wireguard_ip(), "10.0.0.2")

    def test_returns_none_when_pool_exhausted(self):
        self._adopted("10.0.0.1")
        self._adopted("10.0.0.2")
        self.assertIsNone(self.prov.allocate_wireguard_ip())

    def test_returns_none_for_empty_pool(self):
        self.prov.wireguard_address_pool = ""
        self.prov.save()
        self.assertIsNone(self.prov.allocate_wireguard_ip())

    def test_returns_none_for_invalid_pool(self):
        self.prov.wireguard_address_pool = "not-a-cidr"
        self.prov.save()
        self.assertIsNone(self.prov.allocate_wireguard_ip())
