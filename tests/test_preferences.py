import pytest

from kindler.db import Preference, session_factory
from kindler.preferences import load_preferences, preferences, save_preferences

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_saved_preferences_survive_a_reload():
    await save_preferences(kindle_address="me@kindle.com", show_sender_notice=False)
    preferences.kindle_address = ""
    preferences.show_sender_notice = True

    await load_preferences()

    assert preferences.kindle_address == "me@kindle.com"
    assert preferences.show_sender_notice is False


async def test_unknown_preferences_are_refused():
    with pytest.raises(KeyError, match="colour"):
        await save_preferences(colour="red")


async def test_broken_or_mistyped_values_fall_back_to_defaults():
    async with session_factory() as session:
        session.add(Preference(key="kindle_address", value="{broken"))
        session.add(Preference(key="show_sender_notice", value='"yes"'))
        await session.commit()

    await load_preferences()

    assert preferences.kindle_address == ""
    assert preferences.show_sender_notice is True
