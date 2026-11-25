"""Online payment gateway adapter: SkipCash (Qatar).

OFF until keys are set in secrets / env:
    SKIPCASH_ENV        = "sandbox" | "live"
    SKIPCASH_CLIENT_ID  = "..."
    SKIPCASH_KEY_ID     = "..."
    SKIPCASH_KEY_SECRET = "..."
    SKIPCASH_WEBHOOK_KEY = "..."   (only needed for the optional webhook receiver)

!! VERIFY AGAINST SKIPCASH'S DEVELOPER DOCS WITH SANDBOX KEYS BEFORE GOING LIVE !!
The endpoints, field names and signing scheme below follow the publicly described SkipCash merchant
integration (HMAC-SHA256 over the ordered request fields, base64, in the Authorization header). They were
NOT tested against a live SkipCash account. Everything that touches the wire is in this file, so fixing
any mismatch is a change here only.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from urllib import request as urlreq

import db

BASE_URLS = {
    "sandbox": "https://skipcashtest.azurewebsites.net",
    "live": "https://api.skipcash.app",
}
TIMEOUT = 20


class NotConfigured(RuntimeError):
    pass


def _cfg() -> dict:
    return {k: db.secret(k) for k in ("SKIPCASH_ENV", "SKIPCASH_CLIENT_ID", "SKIPCASH_KEY_ID", "SKIPCASH_KEY_SECRET")}


def is_configured() -> bool:
    c = _cfg()
    return all(c.values()) and c["SKIPCASH_ENV"] in BASE_URLS


def mode() -> str:
    return _cfg()["SKIPCASH_ENV"] if is_configured() else "off"


def _sign(fields: list[tuple[str, str]], secret: str) -> str:
    combined = ",".join(f"{k}={v}" for k, v in fields)
    return base64.b64encode(hmac.new(secret.encode(), combined.encode(), hashlib.sha256).digest()).decode()


def _post(path: str, body: dict, auth: str) -> dict:
    req = urlreq.Request(BASE_URLS[_cfg()["SKIPCASH_ENV"]] + path, data=json.dumps(body).encode(),
                         headers={"Content-Type": "application/json", "Authorization": auth}, method="POST")
    with urlreq.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def _get(path: str, auth: str) -> dict:
    req = urlreq.Request(BASE_URLS[_cfg()["SKIPCASH_ENV"]] + path, headers={"Authorization": auth})
    with urlreq.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


def create_pay_link(reference: str, amount: float, first_name: str, last_name: str, phone: str, email: str,
                    description: str = "") -> tuple[str, str]:
    """Create a payment; returns (provider_id, pay_url)."""
    if not is_configured():
        raise NotConfigured("SkipCash keys are not set.")
    c = _cfg()
    uid = str(uuid.uuid4())
    fields = [("Uid", uid), ("KeyId", c["SKIPCASH_KEY_ID"]), ("Amount", f"{amount:.2f}"),
              ("FirstName", first_name or "Parent"), ("LastName", last_name or "-"), ("Phone", phone or ""),
              ("Email", email or ""), ("TransactionId", reference), ("Custom1", description[:50])]
    body = {k: v for k, v in fields}
    res = _post("/api/v1/payments", body, _sign(fields, c["SKIPCASH_KEY_SECRET"]))
    obj = res.get("resultObj") or {}
    if not obj.get("payUrl"):
        raise RuntimeError(f"SkipCash did not return a pay link: {res.get('errorMessage') or res}")
    return str(obj.get("id")), obj["payUrl"]


def payment_status(provider_id: str) -> str:
    """Returns 'paid' | 'pending' | 'failed' | 'cancelled' | the raw status if unknown."""
    if not is_configured():
        raise NotConfigured("SkipCash keys are not set.")
    res = _get(f"/api/v1/payments/{provider_id}", _cfg()["SKIPCASH_CLIENT_ID"])
    raw = str((res.get("resultObj") or {}).get("statusId", (res.get("resultObj") or {}).get("status", ""))).lower()
    return {"2": "paid", "paid": "paid", "0": "pending", "1": "pending", "new": "pending", "pending": "pending",
            "3": "cancelled", "cancelled": "cancelled", "4": "failed", "5": "failed", "failed": "failed"}.get(raw, raw)
