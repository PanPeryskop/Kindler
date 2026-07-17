import mimetypes
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib
from slugify import slugify

from app.config import settings
from app.services.metadata import extract_epub_metadata


async def send_to_kindle(
    path: Path,
    kindle_address: str | None = None,
    *,
    convert: bool = False,
    title: str | None = None,
    author: str | None = None,
) -> None:
    if path.stat().st_size > settings.max_attachment_bytes:
        raise ValueError(
            f"File size is {path.stat().st_size / 1_048_576:.1f} MB, "
            f"limit is {settings.max_attachment_mb} MB."
        )

    attachment_name = path.name
    email_body = ""
    email_subject = "convert" if convert else ""

    if path.suffix.lower() == ".epub":
        extracted_name, extracted_title, extracted_author = extract_epub_metadata(path)
        if title or author:
            title = title or extracted_title
            author = author or extracted_author
            attachment_name = f"{slugify(author, separator='_')}-{slugify(title, separator='_')}.epub"
        else:
            title, author = extracted_title, extracted_author
            attachment_name = extracted_name
        email_subject = email_subject or f"{author} - {title}"
        email_body = f"Title: {title}\nAuthor: {author}"
    else:
        attachment_name = f"{slugify(path.stem)}{path.suffix}"

    msg = EmailMessage()
    msg["From"] = settings.email_host_user or "noreply@example.com"
    msg["To"] = kindle_address or settings.test_email or settings.kindle_address
    msg["Subject"] = email_subject
    msg.set_content(email_body)

    ctype, _ = mimetypes.guess_type(path.name)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    
    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=attachment_name,
    )

    send_kwargs = {
        "hostname": settings.email_host,
        "port": settings.email_port,
        "start_tls": settings.email_use_tls,
    }
    
    if settings.email_host_user and settings.email_host_password:
        send_kwargs["username"] = settings.email_host_user
        send_kwargs["password"] = settings.email_host_password

    await aiosmtplib.send(msg, **send_kwargs)