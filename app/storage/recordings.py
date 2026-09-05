"""Supabase Storage access for call recordings (REST API, secret key).

Uploads MP3s to the private `recordings` bucket and signs short-lived URLs
(the sheet's `recording` column points at a signed URL, not the raw bucket).
"""

import os

import requests


def is_configured() -> bool:
    return bool(os.getenv("SUPABASE_API_URL")) and bool(os.getenv("SUPABASE_SECRET_KEY"))


def _bucket() -> str:
    return os.getenv("RECORDING_BUCKET", "recordings")


def _headers() -> dict:
    # Local Supabase Storage accepts the secret as both the bearer and apikey.
    secret = os.getenv("SUPABASE_SECRET_KEY")
    return {"Authorization": f"Bearer {secret}", "apikey": secret}


def _base_url() -> str:
    return os.getenv("SUPABASE_API_URL", "").rstrip("/")


def ensure_bucket() -> None:
    """Create the recordings bucket if missing (idempotent)."""
    if not is_configured():
        return
    resp = requests.get(
        f"{_base_url()}/storage/v1/bucket", headers=_headers(), timeout=15
    )
    if resp.status_code != 200:
        resp.raise_for_status()
    if _bucket() not in {b.get("name") for b in resp.json()}:
        resp = requests.post(
            f"{_base_url()}/storage/v1/bucket",
            headers=_headers(),
            json={"name": _bucket(), "public": False},
            timeout=15,
        )
        if resp.status_code not in (200, 201, 400):
            resp.raise_for_status()


def upload_bytes(key: str, data: bytes, content_type: str = "audio/mpeg") -> str:
    """Upload raw bytes to <bucket>/<key>. Returns the key."""
    ensure_bucket()
    url = f"{_base_url()}/storage/v1/object/{_bucket()}/{key}"
    resp = requests.post(
        url,
        headers={**_headers(), "Content-Type": content_type},
        data=data,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload failed ({resp.status_code}): {resp.text[:200]}")
    return key


def signed_url(key: str) -> str | None:
    """Return a full, short-lived signed URL for <bucket>/<key>, or None."""
    if not is_configured():
        return None
    ttl = int(os.getenv("RECORDING_SIGNED_URL_TTL", "3600"))
    url = f"{_base_url()}/storage/v1/object/sign/{_bucket()}/{key}"
    resp = requests.post(url, headers=_headers(), json={"expiresIn": ttl}, timeout=15)
    if resp.status_code != 200:
        return None
    signed = (resp.json() or {}).get("signedURL")
    if not signed:
        return None
    return f"{_base_url()}/storage/v1{signed}" if signed.startswith("/") else signed
