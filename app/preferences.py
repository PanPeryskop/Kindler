import json
from dataclasses import dataclass, fields

from sqlmodel import select

from app.db import Preference, session_factory


@dataclass
class Preferences:
    kindle_address: str = ""
    zlib_email: str = ""
    zlib_password: str = ""
    show_sender_notice: bool = True


preferences = Preferences()
PREFERENCE_NAMES = frozenset(field.name for field in fields(Preferences))


def _decode(raw: str) -> object:
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def load_preferences() -> None:
    async with session_factory() as session:
        rows = await session.exec(select(Preference))
        stored = {row.key: _decode(row.value) for row in rows}

    for field in fields(Preferences):
        value = stored.get(field.name)
        valid = isinstance(value, type(field.default))
        setattr(preferences, field.name, value if valid else field.default)


async def save_preferences(**changes: object) -> None:
    unknown = changes.keys() - PREFERENCE_NAMES
    if unknown:
        raise KeyError(f"Unknown preferences: {', '.join(sorted(unknown))}")

    async with session_factory() as session:
        for key, value in changes.items():
            await session.merge(Preference(key=key, value=json.dumps(value)))
        await session.commit()

    for key, value in changes.items():
        setattr(preferences, key, value)
