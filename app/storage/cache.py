"""Redis access for short-lived state (private pre-check jobs). A client is created per event loop."""

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis

from app.config import get_settings

_clients: dict[int, aioredis.Redis] = {}


def client() -> aioredis.Redis:
    loop_id = id(asyncio.get_running_loop())
    if loop_id not in _clients:
        _clients[loop_id] = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _clients[loop_id]


async def close() -> None:
    loop_id = id(asyncio.get_running_loop())
    if (c := _clients.pop(loop_id, None)) is not None:
        await c.aclose()


async def put_json(key: str, value: Any, ttl_s: int) -> None:
    await client().set(key, json.dumps(value, default=str), ex=ttl_s)


async def get_json(key: str) -> Any | None:
    raw = await client().get(key)
    return json.loads(raw) if raw else None
