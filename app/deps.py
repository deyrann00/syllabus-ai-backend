from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import jwt
from app.auth.rbac import Principal, forbidden
from app.config import get_settings
from app.db.pg import get_session
from app.enums import Role
from app.models import User

DbSession = Annotated[AsyncSession, Depends(get_session)]


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(get_settings().cookie_name)


async def get_principal(request: Request, session: DbSession) -> Principal:
    token = _token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        user_id = jwt.decode(token)
    except jwt.InvalidToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    user = await session.scalar(sa.select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is not active")
    return Principal.of(user)


CurrentUser = Annotated[Principal, Depends(get_principal)]


def require_role(*roles: Role):
    async def dependency(principal: CurrentUser) -> Principal:
        if not principal.has(*roles):
            raise forbidden(f"Requires role: {', '.join(r.value for r in roles)}")
        return principal

    return Depends(dependency)
