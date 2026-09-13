from __future__ import annotations

import hashlib
import re


def investor_entity_key(profile: dict) -> str:
    """Return a stable Home Assistant identity derived from the investor PAN.

    PAN is the business identity for an investor in Mutual Fund Tracker. We hash
    the normalized PAN so the raw PAN is not exposed in Home Assistant's entity
    registry, while keeping the value deterministic across database rebuilds,
    restarts, and profile-id reuse.
    """
    pan = str(profile.get("pan") or "").strip().upper()
    compact_pan = re.sub(r"\s+", "", pan)
    if compact_pan:
        return hashlib.sha256(compact_pan.encode("utf-8")).hexdigest()[:16]

    # PAN should normally be present for imported investors. Keep a deterministic
    # fallback for legacy/default profiles that do not have one yet.
    profile_id = profile.get("id")
    return f"legacy_{profile_id}" if profile_id is not None else "legacy_unknown"
