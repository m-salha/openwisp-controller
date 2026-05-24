import django.db.models.deletion
import swapper
import uuid

from django.db import migrations, models

import openwisp_controller.provisioning.models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(
            swapper.get_model_name('openwisp_users', 'Organization')
        ),
    ]

    operations = [
        migrations.CreateModel(
            name='AdoptionToken',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'token',
                    models.CharField(
                        db_index=True,
                        default=openwisp_controller.provisioning.models._generate_token,
                        max_length=128,
                        unique=True,
                        verbose_name='token',
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('active', 'Active'),
                            ('used', 'Used'),
                            ('revoked', 'Revoked'),
                            ('expired', 'Expired'),
                        ],
                        db_index=True,
                        default='active',
                        max_length=16,
                        verbose_name='status',
                    ),
                ),
                (
                    'max_uses',
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name='max uses',
                    ),
                ),
                (
                    'uses_count',
                    models.PositiveIntegerField(
                        default=0,
                        editable=False,
                        verbose_name='uses count',
                    ),
                ),
                (
                    'expires_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name='expires at',
                    ),
                ),
                ('notes', models.TextField(blank=True, verbose_name='notes')),
                (
                    'created',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='created',
                    ),
                ),
                (
                    'modified',
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name='modified',
                    ),
                ),
                (
                    'organization',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='adoption_tokens',
                        to=swapper.get_model_name('openwisp_users', 'Organization'),
                        verbose_name='organization',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Adoption Token',
                'verbose_name_plural': 'Adoption Tokens',
                'ordering': ['-created'],
            },
        ),
        migrations.CreateModel(
            name='OrganizationProvisioningConfig',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ('enabled', models.BooleanField(default=True, verbose_name='enabled')),
                ('controller_url', models.URLField(verbose_name='controller URL')),
                (
                    'mode',
                    models.CharField(
                        choices=[
                            ('openwisp-config', 'OpenWISP Config (shared_secret auto-registration)'),
                            ('vpn-radius', 'VPN + RADIUS + Captive Portal'),
                        ],
                        default='openwisp-config',
                        max_length=32,
                        verbose_name='mode',
                    ),
                ),
                (
                    'wireguard_server_public_key',
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name='WireGuard server public key',
                    ),
                ),
                (
                    'wireguard_server_endpoint',
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name='WireGuard server endpoint',
                    ),
                ),
                (
                    'wireguard_address_pool',
                    models.CharField(
                        blank=True,
                        default='10.100.10.0/24',
                        max_length=64,
                        verbose_name='WireGuard address pool',
                    ),
                ),
                (
                    'wireguard_allowed_ips',
                    models.CharField(
                        blank=True,
                        default='10.100.0.0/16',
                        max_length=255,
                        verbose_name='WireGuard allowed IPs',
                    ),
                ),
                (
                    'wireguard_dns',
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name='WireGuard DNS',
                    ),
                ),
                (
                    'radius_server_ip',
                    models.GenericIPAddressField(
                        blank=True,
                        null=True,
                        verbose_name='RADIUS server IP',
                    ),
                ),
                (
                    'radius_server_port',
                    models.PositiveIntegerField(
                        default=1812,
                        verbose_name='RADIUS auth port',
                    ),
                ),
                (
                    'radius_acct_port',
                    models.PositiveIntegerField(
                        default=1813,
                        verbose_name='RADIUS acct port',
                    ),
                ),
                (
                    'radius_secret',
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name='RADIUS NAS secret',
                    ),
                ),
                (
                    'captive_portal_enabled',
                    models.BooleanField(
                        default=False,
                        verbose_name='captive portal enabled',
                    ),
                ),
                (
                    'captive_portal_uam_server',
                    models.CharField(
                        blank=True,
                        max_length=512,
                        verbose_name='UAM server URL',
                    ),
                ),
                (
                    'captive_portal_uam_port',
                    models.PositiveIntegerField(
                        default=3990,
                        verbose_name='UAM port',
                    ),
                ),
                (
                    'captive_portal_uam_secret',
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name='UAM secret',
                    ),
                ),
                (
                    'captive_portal_nas_id',
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name='NAS identifier',
                    ),
                ),
                (
                    'captive_portal_dhcpif',
                    models.CharField(
                        default='br-lan',
                        max_length=32,
                        verbose_name='DHCP interface',
                    ),
                ),
                (
                    'captive_portal_tundev',
                    models.CharField(
                        default='tun1',
                        max_length=32,
                        verbose_name='tunnel device',
                    ),
                ),
                (
                    'created',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='created',
                    ),
                ),
                (
                    'modified',
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name='modified',
                    ),
                ),
                (
                    'organization',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='provisioning_config',
                        to=swapper.get_model_name('openwisp_users', 'Organization'),
                        verbose_name='organization',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Organization Provisioning Config',
                'verbose_name_plural': 'Organization Provisioning Configs',
            },
        ),
        migrations.CreateModel(
            name='AdoptedDevice',
            fields=[
                (
                    'id',
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    'mac_address',
                    models.CharField(
                        db_index=True,
                        max_length=17,
                        verbose_name='MAC address',
                    ),
                ),
                (
                    'hostname',
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name='hostname',
                    ),
                ),
                (
                    'model',
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name='model',
                    ),
                ),
                (
                    'wireguard_public_key',
                    models.CharField(
                        blank=True,
                        max_length=64,
                        verbose_name='WireGuard public key',
                    ),
                ),
                (
                    'wireguard_assigned_ip',
                    models.GenericIPAddressField(
                        blank=True,
                        null=True,
                        verbose_name='WireGuard assigned IP',
                    ),
                ),
                (
                    'adopted_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='adopted at',
                    ),
                ),
                (
                    'token',
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='adopted_devices',
                        to='provisioning.adoptiontoken',
                        verbose_name='adoption token',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Adopted Device',
                'verbose_name_plural': 'Adopted Devices',
                'ordering': ['-adopted_at'],
            },
        ),
    ]
