from datetime import UTC, datetime

from pydantic import NaiveDatetime
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlmodel import Field, SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from kindler.settings import settings


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
    source: str | None = None
    ref: str | None = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    sent_at: NaiveDatetime | None = None


class Preference(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class ZLibAccount(SQLModel, table=True):
    email: str = Field(primary_key=True)
    password: str
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Database:
    def __init__(self) -> None:
        self._connection: tuple[AsyncEngine, async_sessionmaker[AsyncSession]] | None = None

    def _open(self) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
        if self._connection is None:
            engine = create_async_engine(f"sqlite+aiosqlite:///{settings.db_path}")
            sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            self._connection = engine, sessions
        return self._connection

    @property
    def engine(self) -> AsyncEngine:
        return self._open()[0]

    def session(self) -> AsyncSession:
        return self._open()[1]()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection[0].dispose()
        self._connection = None


database = Database()
session_factory = database.session


def add_missing_columns(connection: Connection) -> None:
    inspector = inspect(connection)
    for table in SQLModel.metadata.sorted_tables:
        existing = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            column_type = column.type.compile(connection.dialect)
            connection.exec_driver_sql(
                f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'
            )


async def init_db() -> None:
    async with database.engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
        await connection.run_sync(add_missing_columns)
