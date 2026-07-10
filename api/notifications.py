"""In-app notifications - core/rule_engine.py's notify_owner action writes
here; web/shell.js polls the unread count for the nav bell. In-app only,
no email/SMS/push - those would need an external provider and
credentials, out of scope here.
"""
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = API_DIR.parent
CORE_DIR = PROJECT_ROOT / "core"
sys.path.insert(0, str(CORE_DIR))

from fastapi import APIRouter, Depends, HTTPException

import database
from auth_routes import get_current_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("")
def list_notifications(unread_only: bool = False, user=Depends(get_current_user)):
    return database.list_notifications(user["id"], unread_only=unread_only)


@router.get("/unread-count")
def unread_count(user=Depends(get_current_user)):
    return {"count": database.count_unread_notifications(user["id"])}


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, user=Depends(get_current_user)):
    notifications = database.list_notifications(user["id"], limit=1000)
    if not any(n["id"] == notification_id for n in notifications):
        raise HTTPException(status_code=404, detail="notification not found")
    database.mark_notification_read(notification_id)
    return {"status": "read"}


@router.post("/read-all")
def mark_all_read(user=Depends(get_current_user)):
    database.mark_all_notifications_read(user["id"])
    return {"status": "read"}
