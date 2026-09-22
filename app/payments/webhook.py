"""Finix webhook authentication and payload extraction."""

import hashlib
import hmac
import time


def verify_finix_signature(raw_body, header, signing_key, max_age_seconds=300, now=None):
    if not signing_key or not header:
        return False
    parts = {}
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if separator:
            parts[key] = value
    timestamp = parts.get("t") or parts.get("timestamp")
    signature = parts.get("v1") or parts.get("signature") or parts.get("sig")
    if not timestamp or not signature:
        return False
    try:
        timestamp_value = int(timestamp)
    except ValueError:
        return False
    current_time = int(time.time() if now is None else now)
    if abs(current_time - timestamp_value) > max_age_seconds:
        return False
    signed = f"{timestamp}:".encode("utf-8") + raw_body
    expected = hmac.new(signing_key.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def extract_transfer(payload):
    """Find a transfer inside supported Finix webhook envelopes."""
    if isinstance(payload, list):
        for value in payload:
            found = extract_transfer(value)
            if found:
                return found
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("id", "")).startswith("TR") and payload.get("state"):
        return payload
    for key in ("entity", "data"):
        found = extract_transfer(payload.get(key))
        if found:
            return found
    embedded = payload.get("_embedded", {})
    if isinstance(embedded, dict):
        for value in embedded.values():
            found = extract_transfer(value)
            if found:
                return found
    return None
