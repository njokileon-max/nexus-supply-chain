# apps/nexus_supply_chain/nexus_supply_chain/fastapi_client.py
"""
One place for everything ERPNext needs to talk to the Crystal FastAPI
service, and for FastAPI to prove it is talking to ERPNext.

The shared secret is the existing `crystal_api_internal_secret` in
site_config.json (also INTERNAL_API_SECRET in FastAPI's .env). It is sent as
the `x-internal-secret` header, the same header /calculate-route and the
production-plan endpoints already check. It is never hardcoded.

`nexus_fastapi_url` in site_config.json may override the base URL (for a
staging FastAPI); otherwise the production URL is used.

Dispatch tickets: desk pages open FastAPI's dispatch WebSocket with a
short-lived ticket signed here with the same secret (HMAC-SHA256). FastAPI
verifies it without calling back, so the socket needs no shared session.

Outgoing pushes (cache invalidation, REFRESH_MANIFESTS, SETTINGS_UPDATED,
the 1-minute roster sync) can be switched off per site with
`"nexus_fastapi_push_enabled": 0` in site_config.json. Use it on a local or
staging copy that points at the LIVE FastAPI: reading (live map, routes,
tickets) keeps working, but nothing done on the copy reaches real phones or
changes live settings. Missing = on.
"""

import base64
import hmac
import json
import time
from hashlib import sha256

import frappe

DEFAULT_FASTAPI_URL = "https://crystal-api.crystalapps.dev"
INTERNAL_SECRET_HEADER = "x-internal-secret"
DISPATCH_TICKET_TTL_SEC = 600


def pushes_enabled():
	"""False on a site with "nexus_fastapi_push_enabled": 0 (local/staging copies)."""
	value = frappe.conf.get("nexus_fastapi_push_enabled")
	if value is None:
		return True
	return str(value).strip().lower() not in ("0", "false", "no", "off")


def push_post(path, **kwargs):
	"""
	requests.post to FastAPI for a PUSH (anything that reaches phones or
	changes FastAPI state), honouring nexus_fastapi_push_enabled. When pushes
	are off it sends nothing and returns a harmless 204 response, so callers
	that check status_code / raise_for_status() / .json() keep working.
	"""
	import requests

	if not pushes_enabled():
		skipped = requests.Response()
		skipped.status_code = 204
		skipped._content = b"{}"
		skipped.url = fastapi_url(path)
		return skipped
	return requests.post(fastapi_url(path), **kwargs)


def internal_secret():
	return (frappe.conf.get("crystal_api_internal_secret") or "").strip()


def internal_headers():
	"""Headers for every ERPNext -> FastAPI call that changes state."""
	secret = internal_secret()
	return {INTERNAL_SECRET_HEADER: secret} if secret else {}


def fastapi_url(path=""):
	base = (frappe.conf.get("nexus_fastapi_url") or DEFAULT_FASTAPI_URL).rstrip("/")
	return f"{base}/{str(path).lstrip('/')}" if path else base


def fastapi_ws_url(path=""):
	"""Same base as fastapi_url, with the WebSocket scheme."""
	url = fastapi_url(path)
	if url.startswith("https://"):
		return "wss://" + url[len("https://"):]
	if url.startswith("http://"):
		return "ws://" + url[len("http://"):]
	return url


def is_internal_request():
	"""True when the current HTTP request carries the correct shared secret.
	Constant-time comparison; False when no secret is configured."""
	secret = internal_secret()
	if not secret:
		return False
	request = getattr(frappe.local, "request", None)
	if request is None:
		return False
	given = (request.headers.get(INTERNAL_SECRET_HEADER) or "").strip()
	return bool(given) and hmac.compare_digest(given, secret)


def make_dispatch_ticket(user, scopes, ttl=DISPATCH_TICKET_TTL_SEC):
	"""
	base64url(json{"u": user, "s": scopes, "exp": epoch}) + "." + hex(HMAC).
	Only good for OPENING a socket within `ttl` seconds; an open socket stays
	open. Returns None when no secret is configured.
	"""
	secret = internal_secret()
	if not secret:
		return None
	claims = {"u": user, "s": sorted(set(scopes)), "exp": int(time.time()) + int(ttl)}
	payload = base64.urlsafe_b64encode(
		json.dumps(claims, separators=(",", ":")).encode()
	).decode().rstrip("=")
	signature = hmac.new(secret.encode(), payload.encode(), sha256).hexdigest()
	return f"{payload}.{signature}"
