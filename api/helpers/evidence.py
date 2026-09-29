"""Evidence-entry validation for the save paths."""

def _evidence_entry_is_valid(entry) -> bool:
    """Decide whether an evidence payload entry is worth persisting.

    Entries with an explicit failure/no-result status are always valid even
    with an empty result body: a legitimate 0-event query or an unavailable
    data source is real execution evidence, not a dropped save. Only a blank
    'success' entry (nothing observed, nothing queried) is skipped.
    """
    if not (getattr(entry, "query_title", "") or "").strip():
        return False
    if (getattr(entry, "result_text", "") or "").strip():
        return True
    if (getattr(entry, "analyst_summary", "") or "").strip():
        return True
    if (getattr(entry, "query_text", "") or "").strip():
        return True
    status = (getattr(entry, "result_status", None) or "success").strip().lower()
    return status not in ("", "success")


