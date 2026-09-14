"""Validation for artifacts before they are used in SPL generation."""

import ipaddress
import re


PROSE_MARKERS = (
    "additional fields",
    "verify this",
    "review this",
    "determine whether",
    "candidate",
    "investigate",
    "authorized",
    "suspicious",
    "adversary",
    "persistence",
)


def _clean(value):
    return " ".join(str(value or "").strip().split())


def _invalid(reason, value):
    return {
        "valid": False,
        "value": value,
        "reason": reason,
    }


def _valid(value):
    return {
        "valid": True,
        "value": value,
        "reason": "",
    }


def validate_artifact_value(artifact_type, value):
    """Return whether an artifact value is safe to use as an SPL pivot."""

    value = _clean(value)
    kind = _clean(artifact_type).lower()

    if not value:
        return _invalid("empty_value", value)

    lower_value = value.lower()

    if any(marker in lower_value for marker in PROSE_MARKERS):
        return _invalid("prose_contamination", value)

    if "####" in value or "[0](http" in lower_value:
        return _invalid("markup_contamination", value)

    if kind in ("host", "hostname", "destination"):
        if re.search(r"\s", value):
            return _invalid("host_contains_whitespace", value)

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,253}", value):
            return _invalid("invalid_host_format", value)

        return _valid(value)

    if kind in ("source_ip", "destination_ip", "ip", "ipv4", "ipv6"):
        try:
            ipaddress.ip_address(value)
            return _valid(value)
        except ValueError:
            return _invalid("invalid_ip_format", value)

    if kind in ("user", "username", "account"):
        if re.search(r"\s", value):
            return _invalid("user_contains_whitespace", value)

        if not re.fullmatch(r"[A-Za-z0-9_.@$-]{1,128}", value):
            return _invalid("invalid_user_format", value)

        return _valid(value)

    if kind in ("process", "parent_process", "file_name"):
        if len(value) > 260:
            return _invalid("artifact_too_long", value)

        lower_process = value.lower()

        # A process-image pivot must be a clean image/path, not a command line,
        # URL, narrative fragment, unknown marker, or multi-token value.
        prohibited_tokens = (
            "<unknown",
            "unknown process",
            "remote url",
            "http://",
            "https://",
            "commandline",
            "event details",
        )

        if any(token in lower_process for token in prohibited_tokens):
            return _invalid("process_contains_non_image_content", value)

        if any(char in value for char in ("\n", "\r", "|", ";", "&", "=")):
            return _invalid("process_contains_command_syntax", value)

        if re.search(r"\s", value):
            return _invalid("process_contains_arguments_or_whitespace", value)

        basename = re.split(r"[\\/]", value)[-1].strip()
        if not basename or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", basename):
            return _invalid("invalid_process_image_format", value)

        return _valid(value)

    return _valid(value)