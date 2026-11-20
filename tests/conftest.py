import os
import tempfile
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("EMAIL_HOST_USER", "sender@example.com")
os.environ.setdefault("EMAIL_HOST_PASSWORD", "app-password")
os.environ.setdefault("TEST_EMAIL", "test@example.com")

TEST_DB = Path(tempfile.gettempdir()) / "kindler_test.db"
TEST_DB.unlink(missing_ok=True)
os.environ.setdefault("DB_PATH", str(TEST_DB))

import pytest  # noqa: E402

from app.db import SQLModel, engine  # noqa: E402
from app.preferences import Preferences, preferences  # noqa: E402


@pytest.fixture(autouse=True)
def default_preferences(monkeypatch):
    for field in fields(Preferences):
        monkeypatch.setattr(preferences, field.name, field.default)


@pytest.fixture
async def clean_db():
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.drop_all)
        await connection.run_sync(SQLModel.metadata.create_all)
    yield
