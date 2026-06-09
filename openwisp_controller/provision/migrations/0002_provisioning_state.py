import uuid

import django.db.models.deletion
import django.utils.timezone
import model_utils.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("provision", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="adoptiontoken",
            name="provisioning_revision",
            field=models.PositiveIntegerField(
                default=1,
                help_text=(
                    "incremented automatically when captive-portal / "
                    "RADIUS settings change"
                ),
                verbose_name="provisioning revision",
            ),
        ),
        migrations.AddField(
            model_name="adoptiontoken",
            name="revision_updated_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="revision updated at"
            ),
        ),
        migrations.CreateModel(
            name="AdoptionDeviceState",
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
                    "mac_address",
                    models.CharField(
                        max_length=17, verbose_name="MAC address"
                    ),
                ),
                (
                    "applied_revision",
                    models.PositiveIntegerField(
                        default=0, verbose_name="applied revision"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unknown", "unknown"),
                            ("needs_provisioning", "needs provisioning"),
                            ("provisioning", "provisioning"),
                            ("provisioned", "provisioned"),
                            ("failed", "failed"),
                        ],
                        default="unknown",
                        max_length=20,
                        verbose_name="status",
                    ),
                ),
                (
                    "last_error",
                    models.CharField(
                        blank=True,
                        help_text="short, non-sensitive error description",
                        max_length=255,
                        verbose_name="last error",
                    ),
                ),
                (
                    "last_seen_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last seen at"
                    ),
                ),
                (
                    "last_adopted_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="last adopted at",
                    ),
                ),
                (
                    "token",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="device_states",
                        to="provision.adoptiontoken",
                        verbose_name="adoption token",
                    ),
                ),
            ],
            options={
                "verbose_name": "Adoption device state",
                "verbose_name_plural": "Adoption device states",
                "ordering": ("-modified",),
                "unique_together": {("token", "mac_address")},
            },
        ),
    ]
