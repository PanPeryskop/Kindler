from dataclasses import fields
from pathlib import Path

import pytest

from kindler.db import database
from kindler.preferences import Preferences, preferences
from portable.store import Store
from portable_tests.support import FakeDevice


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice()


@pytest.fixture
async def store(tmp_path: Path, device: FakeDevice):
    for field in fields(Preferences):
        setattr(preferences, field.name, field.default)
    await database.close()
    store = Store(device, tmp_path)
    await store.start()
    yield store
    await database.close()
