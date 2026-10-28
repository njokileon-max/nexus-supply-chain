"""
Nexus Production service: snapshot -> calculation engine -> cached board.

    result = get_board("confirmed")      # {"board": {...}, "meta": {...}}

* The page never waits for the engine (stale-while-revalidate). When the
  cached board is out of date, the page gets the last good board at once,
  marked "refreshing", and ONE background job recalculates it. When the job
  finishes it tells open pages over the realtime socket and they reload.
  Only the very first board of a date window is calculated while you wait.
* Callers that need the current figures (Work Order / Material Request
  actions, the Settings preview) pass background=False and get a fresh board
  or a clear error, never an old one.
* A Redis lock makes sure only one calculation runs per window.
* Every good board is also kept as the "last good" board. If the engine
  fails, the last good board is shown marked stale, never an empty page.
* After a failure the engine is left alone for ENGINE_BACKOFF_SECONDS.
* Engine calls reuse one HTTPS connection per worker (keep-alive).
* The secret is read from site_config.json: crystal_api_internal_secret
  (nexus_internal_secret is accepted as an alternative name).
"""
import gzip
import json
import time

import frappe
import requests
from frappe.utils import cint, now_datetime

from nexus_supply_chain.production.settings import get_settings
from nexus_supply_chain.production.snapshot import build_snapshot, resolve_window, snapshot_counts

# Cleared automatically when Nexus Production Settings is saved.
CACHE_PREFIX = "nexus_production:"
# Deliberately a different prefix: the last good board survives a Settings save.
LAST_GOOD_PREFIX = "nexus_production_last_good:"
LOCK_PREFIX = "nexus_production_lock:"
# A counter per mode that is part of every board key. Bumping it (one Redis INCR)
# makes every cached board of that mode obsolete at once, with no key scans.
# Old boards are never read again and simply expire.
GEN_PREFIX = CACHE_PREFIX + "gen:"

ENGINE_BACKOFF_SECONDS = 60
# last good boards for custom date windows must not pile up in Redis forever
LAST_GOOD_TTL_SECONDS = 7 * 24 * 3600
CONNECT_TIMEOUT_SECONDS = 5
# background recalculations nobody waits for may take longer than the page's timeout
BACKGROUND_TIMEOUT_SECONDS = 90
QUEUED_PREFIX = CACHE_PREFIX + "queued:"
# same event as signals.EVENT: open pages reload when they receive it
REALTIME_EVENT = "nexus_production_changed"
MODES = ("confirmed", "drafts")

_session = None


class EngineError(Exception):
    """The engine could not produce a board (unreachable, rejected, failed)."""


# ------------------------------------------------------------------ engine

def engine_secret():
    return (frappe.conf.get("crystal_api_internal_secret")
            or frappe.conf.get("nexus_internal_secret") or "")


def _engine_headers():
    secret = engine_secret()
    if not secret:
        raise EngineError("crystal_api_internal_secret is missing from this site's site_config.json.")
    return {"x-internal-secret": secret, "Accept-Encoding": "gzip", "Content-Type": "application/json"}


def _engine_url(s):
    url = (s.engine_url or "").strip()
    if not url:
        raise EngineError("Engine URL is empty in Nexus Production Settings.")
    return url


def _timeout(s):
    return max(5, cint(s.engine_timeout_seconds) or 20)


def _explain(response):
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = None
    detail = detail if isinstance(detail, str) else (json.dumps(detail)[:400] if detail else response.text[:400])
    if response.status_code == 401:
        return ("The engine rejected the secret (401). crystal_api_internal_secret in site_config.json "
                "must equal INTERNAL_API_SECRET in the FastAPI .env.")
    if response.status_code == 404:
        return "The engine URL was not found (404). Check Engine URL in Nexus Production Settings."
    if response.status_code == 422:
        return "The engine refused the data (422): {0}".format(detail)
    return "The engine answered HTTP {0}: {1}".format(response.status_code, detail)


def _http():
    """One keep-alive session per worker process: no new TLS handshake for every calculation."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _post(url, body, headers, timeout):
    try:
        # requests/urllib3 send the request body under the FIRST (connect) timeout,
        # so it must allow for uploading a multi-MB snapshot, not just connecting.
        return _http().post(url, data=body, headers=headers, timeout=(timeout, timeout))
    except requests.Timeout:
        raise EngineError("The engine did not answer within {0} seconds.".format(timeout))
    except requests.RequestException as e:
        raise EngineError("The engine could not be reached: {0}".format(e))


def call_engine(payload, s=None, timeout=None):
    s = s or get_settings()
    url, headers, timeout = _engine_url(s), _engine_headers(), timeout or _timeout(s)
    body = json.dumps(payload, default=str, separators=(",", ":")).encode("utf-8")

    # Upload compressed (~10x smaller). An engine that does not understand
    # compressed uploads refuses them with 400/415/422: send it plain once.
    response = _post(url, gzip.compress(body, compresslevel=6),
                     dict(headers, **{"Content-Encoding": "gzip"}), timeout)
    if response.status_code in (400, 415, 422):
        response = _post(url, body, headers, timeout)
    if response.status_code != 200:
        raise EngineError(_explain(response))
    try:
        board = response.json()
    except ValueError:
        raise EngineError("The engine answered with something that is not JSON.")
    if not isinstance(board, dict) or board.get("status") != "success":
        raise EngineError("The engine answered without a board.")
    return board


def health_check():
    """GET <engine_url>/health with the secret. Returns (ok, message, engine_version)."""
    s = get_settings()
    try:
        url = _engine_url(s).rstrip("/") + "/health"
        response = _http().get(url, headers=_engine_headers(), timeout=(CONNECT_TIMEOUT_SECONDS, 10))
    except EngineError as e:
        return False, str(e), None
    except requests.RequestException as e:
        return False, "The engine could not be reached: {0}".format(e), None
    if response.status_code != 200:
        return False, _explain(response), None
    try:
        version = response.json().get("engine_version")
    except ValueError:
        version = None
    return True, "Connected. Engine version {0}.".format(version or "unknown"), version


# ------------------------------------------------------------------ cache

def _meta(board, *, cached, stale=False, error=None, counts=None, refreshing=False):
    return {
        "refreshing": refreshing,
        "mode": board.get("mode"),
        "calculated_at": board.get("_calculated_at") or board.get("generated_at"),
        "engine_version": board.get("engine_version"),
        "timing_ms": board.get("timing_ms"),
        "cached": cached,
        "stale": stale,
        "error": error,
        "snapshot": counts or board.get("_snapshot_counts"),
        "window": board.get("_window"),
    }


def _public(board):
    """The board without the service's private bookkeeping keys."""
    return {k: v for k, v in board.items() if not k.startswith("_")}


def _result(board, **meta):
    return {"board": _public(board), "meta": _meta(board, **meta)}


def _calculate(mode, s, from_date, to_date, timeout=None):
    payload, window = build_snapshot(mode, from_date, to_date)
    counts = snapshot_counts(payload)
    board = call_engine(payload, s, timeout)
    board["_calculated_at"] = str(now_datetime())
    board["_snapshot_counts"] = counts
    board["_window"] = window
    return board


def _store(board_key, last_key, down_key, board, s):
    cache = frappe.cache()
    ttl = cint(s.cache_ttl_seconds)
    if ttl > 0:
        cache.set_value(board_key, board, expires_in_sec=ttl)
    cache.set_value(last_key, board, expires_in_sec=LAST_GOOD_TTL_SECONDS)
    cache.delete_value(down_key)


def _read(key):
    """Read straight from Redis. expires=True skips Frappe's per-request memory,
    which would otherwise keep returning an early miss while we wait."""
    return frappe.cache().get_value(key, expires=True)


def _fallback(last_key, error):
    last = _read(last_key)
    if last:
        return _result(last, cached=True, stale=True, error=error)
    frappe.throw(
        "The production board could not be calculated and there is no earlier board to show.<br><br>{0}".format(
            frappe.utils.escape_html(error)),
        title="Production board unavailable",
    )


def _generation(mode):
    cache = frappe.cache()
    value = cache.get(cache.make_key(GEN_PREFIX + mode))
    return cint(value) if value else 0


def _keys(mode, s, from_date, to_date):
    f, t, _is_default = resolve_window(from_date, to_date, s)
    window = "{0}:{1}:{2}".format(mode, f, t)
    generation = _generation(mode)
    # the generation is in the board, lock and queue keys: data that changes while a
    # board is being calculated makes the next read calculate again
    return frappe._dict(
        f=f, t=t, window=window,
        board=CACHE_PREFIX + "board:{0}:{1}".format(generation, window),
        down=CACHE_PREFIX + "down:" + mode,          # an engine failure affects every window
        last=LAST_GOOD_PREFIX + window,
        lock="{0}{1}:{2}".format(LOCK_PREFIX, generation, window),
        queued="{0}{1}:{2}".format(QUEUED_PREFIX, generation, window),
    )


def _lock_ttl(timeout):
    return timeout + CONNECT_TIMEOUT_SECONDS + 120   # snapshot + engine, with room to spare


def _run(mode, s, k, timeout):
    """Calculate under the window's lock. Returns ("done", board) | ("busy", None) | ("failed", error)."""
    cache = frappe.cache()
    if not cache.set(cache.make_key(k.lock), 1, ex=_lock_ttl(timeout), nx=True):
        return "busy", None
    trace = None
    try:
        board = _calculate(mode, s, k.f, k.t, timeout)
        _store(k.board, k.last, k.down, board, s)
        return "done", board
    except EngineError as e:
        error = str(e)
    except frappe.ValidationError:
        raise                       # settings incomplete etc.: show the real message
    except Exception as e:
        error = "Unexpected error while building the board: {0}".format(e)
        trace = frappe.get_traceback()
    finally:
        cache.delete_value(k.lock)
    cache.set_value(k.down, error, expires_in_sec=ENGINE_BACKOFF_SECONDS)
    frappe.log_error(title="Nexus Production board failed ({0})".format(mode), message=trace or error)
    return "failed", error


def _queue_refresh(mode, k, force=False):
    """At most one background recalculation per window at a time."""
    cache = frappe.cache()
    if cache.get(cache.make_key(k.lock)):
        return                                       # already calculating
    if not cache.set(cache.make_key(k.queued), 1, ex=_lock_ttl(BACKGROUND_TIMEOUT_SECONDS), nx=True):
        return                                       # already queued
    frappe.enqueue("nexus_supply_chain.production.service.refresh_board", queue="default",
                   timeout=_lock_ttl(BACKGROUND_TIMEOUT_SECONDS) + 60,
                   mode=mode, from_date=str(k.f), to_date=str(k.t), force=1 if force else 0)


def refresh_board(mode, from_date, to_date, force=0):
    """Background job: recalculate one window, then tell open pages to reload."""
    s = get_settings()
    k = _keys(mode, s, from_date, to_date)
    try:
        if not cint(force) and (_read(k.board) or _read(k.down)):
            return                                   # already fresh, or the engine is resting
        status, _ = _run(mode, s, k, max(_timeout(s), BACKGROUND_TIMEOUT_SECONDS))
    finally:
        frappe.cache().delete_value(k.queued)
    if status in ("done", "failed"):
        # pages reload: a new board, or the last one marked stale with the reason
        frappe.publish_realtime(REALTIME_EVENT, {"modes": [mode], "ready": 1, "status": status,
                                                 "window": [str(k.f), str(k.t)]})


def get_board(mode="confirmed", refresh=False, from_date=None, to_date=None, background=True):
    """
    The board for 'confirmed' or 'drafts' over a confirmed-on date window
    (default: the last Settings > Count Confirmed Orders From Last (Days) days).
    Access must be checked by the caller.

    background=True (the page): never waits for the engine when an earlier board
    of this window exists; refresh=True then only forces a background recalculation.
    background=False (actions, previews): returns a board calculated from the current
    data, or the last good one marked stale with the error.
    """
    mode = mode if mode in MODES else "confirmed"
    s = get_settings()
    k = _keys(mode, s, from_date, to_date)

    if not refresh:
        hit = _read(k.board)
        if hit:
            return _result(hit, cached=True)

    down = _read(k.down)
    if background:
        last = _read(k.last)
        if last:
            if refresh or not down:
                _queue_refresh(mode, k, force=refresh)
            return _result(last, cached=True, stale=bool(down), error=down, refreshing=not down or refresh)

    if down and not refresh:
        return _fallback(k.last, down)

    timeout = _timeout(s)
    status, value = _run(mode, s, k, timeout)
    if status == "done":
        return _result(value, cached=False)
    if status == "failed":
        return _fallback(k.last, value)

    # someone else is calculating this window: wait for their result
    deadline = time.time() + timeout + CONNECT_TIMEOUT_SECONDS + 30
    while time.time() < deadline:
        time.sleep(0.5)
        hit = _read(k.board)
        if hit:
            return _result(hit, cached=True)
        if _read(k.down):
            break
    return _fallback(k.last, _read(k.down) or "The calculation is taking longer than expected.")


def clear_board_cache(modes=None):
    """
    Make every cached board of these modes (default: all) obsolete. O(1) per mode:
    one INCR, no key scans, safe to call on every relevant document change.
    The last good boards are kept for the engine-down fallback.
    """
    cache = frappe.cache()
    for mode in (modes or MODES):
        if mode in MODES:
            cache.incr(cache.make_key(GEN_PREFIX + mode))
