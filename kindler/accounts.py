from sqlmodel import col, select

from kindler.db import ZLibAccount, session_factory
from kindler.preferences import preferences, save_preferences


async def list_accounts() -> list[ZLibAccount]:
    async with session_factory() as session:
        result = await session.exec(select(ZLibAccount).order_by(col(ZLibAccount.created_at)))
        return list(result)


async def get_account(email: str) -> ZLibAccount | None:
    async with session_factory() as session:
        return await session.get(ZLibAccount, email)


async def active_account() -> ZLibAccount | None:
    if not preferences.zlib_active:
        return None
    return await get_account(preferences.zlib_active)


async def save_account(email: str, password: str) -> None:
    async with session_factory() as session:
        account = await session.get(ZLibAccount, email) or ZLibAccount(email=email, password="")
        account.password = password
        session.add(account)
        await session.commit()


async def remove_account(email: str) -> None:
    async with session_factory() as session:
        account = await session.get(ZLibAccount, email)
        if account:
            await session.delete(account)
            await session.commit()


async def activate(email: str) -> bool:
    if email == preferences.zlib_active:
        return False
    await save_preferences(zlib_active=email)
    return True
