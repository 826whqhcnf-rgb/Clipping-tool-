"""Optional YouTube uploader using OAuth 2.0 device flow.

Device flow is used (instead of a redirect-based flow) because it needs no
fixed redirect URL — ideal for Codespaces, where the forwarded URL changes.
The user authorizes on any device by entering a short code; we store the
resulting refresh token and reuse it for uploads.

Everything here is optional: if the Google deps or client credentials are
missing, the app runs fine and the feature is simply unavailable.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

TOKEN_PATH = DATA_DIR / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

VALID_PRIVACY = {"public", "unlisted", "private"}

# Holds the in-progress device-flow code between /connect and /poll calls.
_pending: dict = {}
_lock = threading.Lock()


def _credentials_pair() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get("YOUTUBE_CLIENT_ID"), os.environ.get("YOUTUBE_CLIENT_SECRET")


def is_configured() -> bool:
    """True if OAuth client credentials are available."""
    cid, csec = _credentials_pair()
    return bool(cid and csec)


def is_connected() -> bool:
    """True if we hold a refresh token (the user has authorized)."""
    return TOKEN_PATH.exists()


def disconnect() -> None:
    TOKEN_PATH.unlink(missing_ok=True)


def start_device_flow() -> dict:
    """Ask Google for a device + user code. Returns the verification details."""
    import httpx

    cid, _ = _credentials_pair()
    resp = httpx.post(DEVICE_CODE_URL, data={"client_id": cid, "scope": " ".join(SCOPES)}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    with _lock:
        _pending["device_code"] = data["device_code"]
        _pending["interval"] = data.get("interval", 5)
    return {
        "user_code": data["user_code"],
        "verification_url": data.get("verification_url") or data.get("verification_uri"),
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in", 1800),
    }


def poll_device() -> str:
    """Poll once for the token. Returns 'connected' | 'pending' | 'expired' | error."""
    import httpx

    with _lock:
        device_code = _pending.get("device_code")
    if not device_code:
        return "no_flow"

    cid, csec = _credentials_pair()
    resp = httpx.post(TOKEN_URL, data={
        "client_id": cid, "client_secret": csec,
        "device_code": device_code, "grant_type": DEVICE_GRANT,
    }, timeout=20)
    data = resp.json()

    if "refresh_token" in data:
        TOKEN_PATH.write_text(json.dumps({"refresh_token": data["refresh_token"]}))
        with _lock:
            _pending.clear()
        return "connected"

    error = data.get("error", "error")
    if error in ("authorization_pending", "slow_down"):
        return "pending"
    if error == "expired_token":
        return "expired"
    return error


def _build_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    cid, csec = _credentials_pair()
    refresh_token = json.loads(TOKEN_PATH.read_text())["refresh_token"]
    creds = Credentials(
        token=None, refresh_token=refresh_token, token_uri=TOKEN_URL,
        client_id=cid, client_secret=csec, scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(path: Path, title: str, description: str, tags: list[str], privacy: str = "unlisted") -> str:
    """Upload a video file to YouTube. Returns the new video id."""
    from googleapiclient.http import MediaFileUpload

    if privacy not in VALID_PRIVACY:
        privacy = "unlisted"

    youtube = _build_client()
    body = {
        "snippet": {
            "title": (title or "Short")[:100],
            "description": (description or "")[:4900],
            "tags": [t[:30] for t in (tags or [])][:15],
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _status, response = request.next_chunk()
    return response["id"]
