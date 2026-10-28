from beanie import init_beanie
from pymongo import AsyncMongoClient

from app.config import get_settings
from app.documents import DOCUMENT_MODELS

_client: AsyncMongoClient | None = None


async def init_mongo(url: str | None = None, db_name: str | None = None) -> AsyncMongoClient:
    """Bind Beanie to a fresh client. Called once per event loop (API startup, each Celery task, tests)."""
    global _client
    settings = get_settings()
    _client = AsyncMongoClient(url or settings.mongo_url, tz_aware=True, uuidRepresentation="standard")
    await init_beanie(database=_client[db_name or settings.mongo_db], document_models=DOCUMENT_MODELS)
    return _client


async def close_mongo() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
