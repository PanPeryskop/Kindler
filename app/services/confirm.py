import asyncio
import imaplib
from datetime import datetime, timedelta, timezone

from app.config import settings

FAILURE_HINTS = (
    "problem",
    "unable",
    "could not",
    "couldn't",
    "undeliverable",
    "rejected",
    "not been sent",
)

POLL_ATTEMPTS = 12
POLL_INTERVAL_SECONDS = 15


def _fetch_subject(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> str:
    status, data = imap.fetch(msg_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return ""
    return data[0][1].decode("utf-8", "replace").lower()


def _search(since: datetime, title: str) -> bool | None:
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.email_host_user, settings.email_host_password)
        imap.select("INBOX")
        status, data = imap.search(None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", "amazon")
        if status != "OK":
            return None
        for msg_id in reversed(data[0].split()):
            subject = _fetch_subject(imap, msg_id)
            if "kindle" not in subject and (not title or title.lower() not in subject):
                continue
            if any(hint in subject for hint in FAILURE_HINTS):
                return False
            return True
    return None


async def confirm_delivery(title: str | None) -> bool | None:
    if not (settings.email_host_user and settings.email_host_password):
        return None
    since = datetime.now(timezone.utc) - timedelta(minutes=2)
    for _ in range(POLL_ATTEMPTS):
        try:
            result = await asyncio.to_thread(_search, since, title or "")
        except Exception:
            return None
        if result is not None:
            return result
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None
