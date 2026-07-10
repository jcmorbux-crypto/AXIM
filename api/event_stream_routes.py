"""Real-time event stream (docs/AXIM_REMOTE_ACCESS.md) - GET /api/events/stream
tails core/database.py's server_events outbox (written by core/event_stream.py,
running in the SEPARATE Telegram listener process - see that module's own
docstring for why a DB-mediated bridge is needed at all) and pushes new rows
to every connected client as Server-Sent Events.

One shared background poller task fans out to N per-connection asyncio.Queue
objects, rather than each connection polling the DB independently - required
once multiple simultaneous clients are in scope (docs/AXIM_REMOTE_ACCESS.md),
or DB query volume would multiply linearly with connected clients instead of
staying constant.
"""
import asyncio
import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from typing import Optional

from fastapi import APIRouter, Cookie, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

import database
from auth_routes import _BLOCKED_ACCESS_STATES, _extract_bearer_token

router = APIRouter(prefix="/api/events", tags=["events"])


def _resolve_stream_user(request, authorization, axim_session, token_query_param):
    """Same resolution core/auth_routes.py's get_current_user does
    (header, then cookie), PLUS a ?token= query param as a last resort -
    the browser's native EventSource API cannot set custom headers, so a
    non-cookie (token-mode) client has no other way to authenticate this
    one endpoint specifically. Every other route stays header/cookie-only
    (a query-param token is a weaker transport - it can end up in server
    access logs - so it's deliberately not offered anywhere a client
    could just use fetch() and set a header instead)."""
    raw_token = _extract_bearer_token(authorization) or axim_session or token_query_param
    if not raw_token:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = database.get_session_user(raw_token)
    if user is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    user = database.check_and_expire_trial(user)
    if user["access_state"] in _BLOCKED_ACCESS_STATES:
        raise HTTPException(status_code=403, detail=f"account is {user['access_state']}")
    return user

POLL_INTERVAL_SECONDS = 0.4
KEEPALIVE_SECONDS = 15

# Module-level (per API process, single uvicorn worker) - _subscribers is
# the fan-out registry, _poller_task is the one background task that ever
# reads server_events. Lazily started on the first connection rather than
# a FastAPI startup hook, since this file has no other reason to run code
# at import time.
_subscribers = set()
_poller_task = None
_poller_cursor = None


async def _poller_loop():
    global _poller_cursor
    if _poller_cursor is None:
        _poller_cursor = database.latest_server_event_id() or 0
    while True:
        try:
            rows = database.list_server_events_since(_poller_cursor, limit=200)
            if rows:
                _poller_cursor = rows[-1]["id"]
                for queue in list(_subscribers):
                    for row in rows:
                        queue.put_nowait(row)
        except Exception:
            pass
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _ensure_poller_started():
    global _poller_task
    if _poller_task is None or _poller_task.done():
        _poller_task = asyncio.create_task(_poller_loop())


def _format_sse(event_id, event_type, payload):
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload)}")
    return "\n".join(lines) + "\n\n"


# Most bridged events (trade.*, signal.ignored) describe the one shared
# Telegram/Pocket Option connection (see api/admin.py's module docstring)
# and are legitimately visible to every connected user, matching what the
# equivalent REST endpoints already return unscoped. notification.created
# is the one exception - core/database.py's notifications table is
# per-recipient (a user's own alerts, e.g. "your fund hit its loss
# limit"), so without this filter every connected client's SSE stream
# would receive every OTHER user's notification text too, even though
# the notification bell only ever acts on its own unread count. Filtered
# at read time (both backfill and live) rather than by the poller, since
# the poller has no per-subscriber identity and doesn't need one for
# every other event type.
def _visible_to(user_id, event_type, payload):
    if event_type == "notification.created":
        return isinstance(payload, dict) and payload.get("user_id") == user_id
    return True


async def _event_generator(request, resume_from_id, user_id):
    queue = asyncio.Queue()
    _ensure_poller_started()
    _subscribers.add(queue)
    last_yielded_id = None
    try:
        if resume_from_id is not None and resume_from_id > 0:
            # A gap exists when the id right after what the client last
            # saw is no longer the oldest surviving row (something in
            # between was pruned before the client could see it) - NOT
            # simply "resume_from_id is old", since resume_from_id ==
            # oldest - 1 means the very next row is still intact and
            # nothing was actually lost.
            oldest = database.oldest_server_event_id()
            if oldest is None or resume_from_id < oldest - 1:
                # A genuine gap - tell the client to re-fetch current
                # state via normal REST rather than silently skipping it.
                yield _format_sse(None, "resync", {})
            else:
                for row in database.list_server_events_since(resume_from_id):
                    last_yielded_id = row["id"]
                    if _visible_to(user_id, row["event_type"], row["payload"]):
                        yield _format_sse(row["id"], row["event_type"], row["payload"])
        elif resume_from_id == 0:
            # Explicit "I have no prior state, send me everything you
            # currently have" - never a gap regardless of pruning, since
            # a resume_from_id=0 client never had anything to lose.
            for row in database.list_server_events_since(0):
                last_yielded_id = row["id"]
                if _visible_to(user_id, row["event_type"], row["payload"]):
                    yield _format_sse(row["id"], row["event_type"], row["payload"])
        # resume_from_id is None (no Last-Event-ID/param at all): a
        # genuinely fresh connection with no resume intent - no backfill,
        # just start listening live from this point forward.

        while True:
            if await request.is_disconnected():
                break
            try:
                row = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            # The queue was registered before the backfill read above, so a
            # row landing in that narrow window could appear in both - skip
            # anything already yielded during backfill rather than duplicate it.
            if last_yielded_id is not None and row["id"] <= last_yielded_id:
                continue
            if _visible_to(user_id, row["event_type"], row["payload"]):
                yield _format_sse(row["id"], row["event_type"], row["payload"])
    finally:
        _subscribers.discard(queue)


@router.get("/stream")
async def stream_events(request: Request, last_event_id: Optional[int] = None, token: Optional[str] = None,
                         authorization: Optional[str] = Header(default=None),
                         axim_session: Optional[str] = Cookie(default=None),
                         last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID")):
    """Deliberately gated the same as get_current_user (any logged-in
    user, not require_admin) - any Remote Client sees live notifications/
    status, matching the existing Emergency-Stop precedent that safety/
    visibility isn't owner/admin-only. Not using the shared
    Depends(get_current_user) directly because this route ALSO accepts a
    ?token= query param (see _resolve_stream_user) that no other route
    offers. Accepts the resume cursor as either a ?last_event_id= query
    param (for a client's first connection, e.g. after a page reload
    restoring its last-known id) or the standard Last-Event-ID header
    (which a browser's native EventSource sends automatically on its own
    auto-reconnect)."""
    user = _resolve_stream_user(request, authorization, axim_session, token)

    resume_from = last_event_id
    if resume_from is None and last_event_id_header is not None:
        try:
            resume_from = int(last_event_id_header)
        except ValueError:
            resume_from = None
    return StreamingResponse(
        _event_generator(request, resume_from, user["id"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
