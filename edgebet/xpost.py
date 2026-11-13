"""Post to X (Twitter) as your own account, with a picture. OAuth 1.0a user keys from
the X developer portal: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.

No links in the text: on X's pay-per-use API a post with a URL costs much more,
so the Telegram link goes in the profile bio instead.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import uuid

TWEET_URL = "https://api.x.com/2/tweets"
MEDIA_URLS = ("https://api.x.com/2/media/upload", "https://upload.twitter.com/1.1/media/upload.json")


def _keys():
    k = {n: (os.environ.get(n) or "").strip().strip('"').strip("'")
         for n in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")}
    for n, v in k.items():  # tolerate "X_API_KEY=..." pasted into the secret
        if v.upper().startswith(n + "="):
            k[n] = v.split("=", 1)[1].strip()
    return k


def configured() -> bool:
    return all(_keys().values())


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="~")


def _auth(method: str, url: str, params: dict | None = None) -> str:
    """OAuth 1.0a header. JSON and multipart bodies are not part of the signature."""
    k = _keys()
    oauth = {"oauth_consumer_key": k["X_API_KEY"], "oauth_nonce": secrets.token_hex(16),
             "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(int(time.time())),
             "oauth_token": k["X_ACCESS_TOKEN"], "oauth_version": "1.0"}
    allp = {**(params or {}), **oauth}
    base = "&".join([method.upper(), _q(url), _q("&".join(f"{_q(a)}={_q(b)}" for a, b in sorted(allp.items())))])
    key = f"{_q(k['X_API_SECRET'])}&{_q(k['X_ACCESS_SECRET'])}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_q(a)}="{_q(b)}"' for a, b in sorted(oauth.items()))


def _send(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"X refused: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from None


def upload_png(png: bytes) -> str:
    """Upload a picture, return its media id (tries X's v2 upload, then the older v1.1)."""
    last = None
    for url in MEDIA_URLS:
        boundary = uuid.uuid4().hex
        fields = {"media_category": "tweet_image"}
        body = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in fields.items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="media"; filename="card.png"\r\n'
                 f"Content-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": _auth("POST", url), "Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            resp = _send(req)
            mid = (resp.get("data") or {}).get("id") or resp.get("media_id_string")
            if mid:
                return str(mid)
            last = RuntimeError(f"no media id in {resp}")
        except RuntimeError as e:
            last = e
    raise last or RuntimeError("upload failed")


def post(text: str, png: bytes | None = None) -> str:
    """Post (with the picture if given; text only if the picture upload fails). Returns the post id."""
    payload = {"text": text}
    if png:
        try:
            payload["media"] = {"media_ids": [upload_png(png)]}
        except Exception as e:  # noqa: BLE001
            print("X picture upload failed, posting text only:", e)
    req = urllib.request.Request(TWEET_URL, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": _auth("POST", TWEET_URL), "Content-Type": "application/json"})
    return str(_send(req)["data"]["id"])
