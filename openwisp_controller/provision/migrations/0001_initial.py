import re
import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
import swapper
from django.db import migrations, models

import openwisp_utils.fields
import openwisp_utils.utils


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        swapper.dependency("openwisp_users", "Organization"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdoptionToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created",
                    model_utils.fields.AutoCreatedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="created",
                    ),
                ),
                (
                    "modified",
                    model_utils.fields.AutoLastModifiedField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="modified",
                    ),
                ),
                (
                    "token",
                    openwisp_utils.fields.KeyField(
                        db_index=True,
                        default=openwisp_utils.utils.get_random_key,
                        help_text=(
                            "secret value presented by the router at "
                            "adoption time"
                        ),
                        max_length=64,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                re.compile("^[^\\s/\\.]+$"),
                                code="invalid",
                                message=(
                                    "This value must not contain spaces, "
                                    "dots or slashes."
                                ),
                            )
                        ],
                        verbose_name="token",
                    ),
                ),
                (
                    "description",
                    models.CharField(
                        blank=True,
                        help_text="admin-visible label for this token",
                        max_length=128,
                        verbose_name="description",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="active"),
                ),
                (
                    "max_uses",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="leave blank for unlimited",
                        null=True,
                        verbose_name="max uses",
                    ),
                ),
                (
                    "use_count",
                    models.PositiveIntegerField(
                        default=0, verbose_name="use count"
                    ),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="expires at"
                    ),
                ),
                (
                    "last_used_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last used at"
                    ),
                ),
                (
                    "last_used_mac",
                    models.CharField(
                        blank=True,
                        max_length=17,
                        verbose_name="last used by MAC",
                    ),
                ),
                (
                    "radius_server",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="RADIUS server"
                    ),
                ),
                (
                    "radius_secret",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="RADIUS shared secret",
                    ),
                ),
                (
                    "uam_server",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="UAM server URL",
                    ),
                ),
                (
                    "uam_allowed",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            "list of allow-listed captive-portal hostnames"
                        ),
                        verbose_name="UAM allowed domains",
                    ),
                ),
                (
                    "chilli_net",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=64,
                        verbose_name="Chilli network",
                    ),
                ),
                (
                    "chilli_uamlisten",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=64,
                        verbose_name="Chilli UAM listen address",
                    ),
                ),
                (
                    "chilli_uamport",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=10,
                        verbose_name="Chilli UAM port",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adoption_tokens",
                        to=swapper.get_model_name(
                            "openwisp_users", "Organization"
                        ),
                        verbose_name="organization",
                    ),
                ),
            ],
            options={
                "verbose_name": "Adoption token",
                "verbose_name_plural": "Adoption tokens",
                "ordering": ("-created",),
            },
        ),
    ]
