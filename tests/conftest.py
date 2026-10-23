import asyncio
import os
import sys
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://slotwise:slotwise@localhost:5432/slotwise",
)
os.environ["DB_POOL_SIZE"] = "10"
os.environ["DB_MAX_OVERFLOW"] = "60"
os.environ["BOOKING_STRATEGY"] = "row_lock"


@pytest.fixture(scope="session", autouse=True)
def migrated() -> None:
    from slotwise.config import reset_settings

    reset_settings()
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
async def clean_tables() -> AsyncIterator[None]:
    from slotwise.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE bookings, slots, resources RESTART IDENTITY CASCADE"))
    await engine.dispose()
    yield


@pytest.fixture(autouse=True)
def booking_strategy() -> Iterator[None]:
    previous = os.environ.get("BOOKING_STRATEGY")
    os.environ["BOOKING_STRATEGY"] = "row_lock"
    from slotwise.config import reset_settings

    reset_settings()
    yield
    if previous is None:
        os.environ.pop("BOOKING_STRATEGY", None)
    else:
        os.environ["BOOKING_STRATEGY"] = previous
    reset_settings()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from slotwise.config import reset_settings
    from slotwise.db.session import dispose_engine
    from slotwise.main import create_app

    reset_settings()
    await dispose_engine()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", timeout=30.0) as http:
        yield http
    await dispose_engine()
