import os
import sys
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


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

    monkeypatch.setenv("EMAIL_HOST", "localhost")
    monkeypatch.setenv("EMAIL_PORT", str(unused_tcp_port))
    monkeypatch.setenv("EMAIL_USE_TLS", "False")
    monkeypatch.setenv("EMAIL_HOST_USER", "")
    monkeypatch.setenv("EMAIL_HOST_PASSWORD", "")
    monkeypatch.setenv("KINDLE_ADDRESS", "kindle@example.com")
    monkeypatch.setenv("TEST_EMAIL", os.environ.get("TEST_EMAIL", "test@example.com"))

    yield handler
    controller.stop()


@pytest.mark.asyncio
async def test_send_to_kindle(tmp_path: Path, smtp_sink):
    try:
        from app.services.sender import send_to_kindle
    except ModuleNotFoundError:
        from app.services.sender import send_to_kindle

    f = tmp_path / "test book.epub"
    f.write_bytes(b"fake epub content")

    await send_to_kindle(f)

    assert smtp_sink.envelope is not None
    content = bytes(smtp_sink.envelope.original_content)
    assert b"test-book.epub" in content
    assert os.environ["TEST_EMAIL"].encode() in content