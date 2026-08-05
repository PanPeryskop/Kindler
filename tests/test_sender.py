from pathlib import Path

import fitz
import pytest
from aiosmtpd.controller import Controller

from app.config import settings
from app.services.sender import send_to_kindle


class SinkHandler:
    def __init__(self) -> None:
        self.envelope = None

    async def handle_DATA(self, server, session, envelope):
        self.envelope = envelope
        return "250 OK"


@pytest.fixture
def smtp_sink(monkeypatch, unused_tcp_port):
    handler = SinkHandler()
    controller = Controller(handler, hostname="localhost", port=unused_tcp_port)
    controller.start()

    monkeypatch.setattr(settings, "email_host", "localhost")
    monkeypatch.setattr(settings, "email_port", unused_tcp_port)
    monkeypatch.setattr(settings, "email_use_tls", False)
    monkeypatch.setattr(settings, "email_host_user", "")
    monkeypatch.setattr(settings, "email_host_password", "")
    monkeypatch.setattr(settings, "test_email", "test@example.com")

    yield handler
    controller.stop()


def sent_bytes(sink) -> bytes:
    assert sink.envelope is not None
    return bytes(sink.envelope.original_content)


async def test_send_to_kindle(tmp_path: Path, smtp_sink):
    f = tmp_path / "test book.epub"
    f.write_bytes(b"fake epub content")

    await send_to_kindle(f)

    content = sent_bytes(smtp_sink)
    assert b"test-book.epub" in content
    assert b"test@example.com" in content


async def test_unreadable_epub_falls_back_to_stem(tmp_path: Path, smtp_sink):
    f = tmp_path / "broken book.epub"
    f.write_bytes(b"not really an epub")

    await send_to_kindle(f)

    content = sent_bytes(smtp_sink)
    assert b"broken-book.epub" in content
    assert b"Unknown Author - broken book" in content


async def test_pdf_uses_edited_title_and_author(tmp_path: Path, smtp_sink):
    f = tmp_path / "scan_001.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.save(str(f))
    doc.close()

    await send_to_kindle(f, convert=True, title="Ostatnie Zyczenie", author="A. Sapkowski")

    content = sent_bytes(smtp_sink)
    assert b"a_sapkowski-ostatnie_zyczenie.pdf" in content
    assert b"Subject: convert" in content


async def test_pdf_without_edits_keeps_filename(tmp_path: Path, smtp_sink):
    f = tmp_path / "scan_002.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.save(str(f))
    doc.close()

    await send_to_kindle(f, convert=True)

    assert b"scan-002.pdf" in sent_bytes(smtp_sink)


async def test_oversized_file_is_rejected(tmp_path: Path, smtp_sink, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    f = tmp_path / "big.epub"
    f.write_bytes(b"x" * (2 * 1024 * 1024))

    with pytest.raises(ValueError, match="limit is 1 MB"):
        await send_to_kindle(f)
