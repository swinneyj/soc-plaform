"""Storage-convention utilities shared by the API, service, and DB layers."""


def utcnow_naive():
    """Naive UTC now — timezone-aware internally, stripped to match the
    platform's naive-UTC storage convention (and to avoid the deprecated
    datetime.utcnow())."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)
