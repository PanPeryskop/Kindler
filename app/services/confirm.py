import asyncio
import imaplib
import time
from datetime import datetime
from email.header import decode_header, make_header

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

WATCH_SECONDS = 90
POLL_INTERVAL_SECONDS = 10
IMAP_TIMEOUT_SECONDS = 20


def _decode_subject(raw: bytes) -> str:
    _, _, value = raw.decode("utf-8", "replace").partition(":")
    try:
        return str(make_header(decode_header(value.strip()))).lower()
    except Exception:
        return value.strip().lower()


def _find_problem_report(sent_at: datetime) -> str | None:
    with imaplib.IMAP4_SSL(
        settings.imap_host, settings.imap_port, timeout=IMAP_TIMEOUT_SECONDS
    ) as imap:
        imap.login(settings.email_host_user, settings.email_host_password)
        imap.select("INBOX")

        status, data = imap.search(None, "SINCE", sent_at.strftime("%d-%b-%Y"), "FROM", "amazon")
        if status != "OK" or not data or not data[0]:
            return None

        for msg_id in reversed(data[0].split()):
            status, parts = imap.fetch(msg_id, "(INTERNALDATE BODY[HEADER.FIELDS (SUBJECT)])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue

            received = imaplib.Internaldate2tuple(parts[0][0])
            if not received or time.mktime(received) < sent_at.timestamp():
                continue

            subject = _decode_subject(parts[0][1])
            if "kindle" in subject and any(hint in subject for hint in FAILURE_HINTS):
                return subject
    return None


async def watch_for_problem(sent_at: datetime) -> str | None:
    if not (settings.email_host_user and settings.email_host_password):
        return None

    deadline = time.monotonic() + WATCH_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        try:
            problem = await asyncio.to_thread(_find_problem_report, sent_at)
        except Exception:
            return None
        if problem:
            return problem
    return None
