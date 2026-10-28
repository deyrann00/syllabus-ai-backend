"""Run async service code from a synchronous Celery task: fresh loop, NullPool engine and Mongo client per job."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from app.db import pg
from app.db.mongo import close_mongo, init_mongo
from app.storage import cache


def run(fn: Callable[..., Awaitable[Any]], *args: Any) -> Any:
    async def main() -> Any:
        engine = pg.make_engine(nullpool=True)
        pg.configure(engine)
        await init_mongo()
        try:
            return await fn(*args)
        finally:
            await engine.dispose()
            await close_mongo()
            await cache.close()

    return asyncio.run(main())
