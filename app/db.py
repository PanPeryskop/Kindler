from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    filename: str
    status: str = "pending"
    error: str | None = None
    kindle_address: str | None = None
    tmp_path: str | None = None
    title: str | None = None
    author: str | None = None
    cover: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
