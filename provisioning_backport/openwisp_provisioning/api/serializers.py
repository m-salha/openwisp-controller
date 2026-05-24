import re

from rest_framework import serializers

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_WG_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")


class AdoptionRequestSerializer(serializers.Serializer):
    token = serializers.CharField(
        max_length=128,
        help_text="Adoption token issued by the organisation admin.",
    )
    mac_address = serializers.CharField(
        max_length=17,
        help_text="Primary MAC address in XX:XX:XX:XX:XX:XX format.",
    )
    hostname = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
    )
    model = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
        help_text="Hardware model string, e.g. x86_64-vbox.",
    )
    public_wireguard_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
        help_text="WireGuard public key (base64, 44 chars). Optional.",
    )

    def validate_mac_address(self, value):
        if not _MAC_RE.match(value):
            raise serializers.ValidationError(
                "Invalid MAC address. Expected XX:XX:XX:XX:XX:XX."
            )
        return value.upper()

    def validate_public_wireguard_key(self, value):
        if value and not _WG_KEY_RE.match(value):
            raise serializers.ValidationError(
                "Invalid WireGuard public key. Expected 44-char base64 string."
            )
        return value
