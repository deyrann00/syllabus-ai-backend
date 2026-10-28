import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from app.auth import jwt
from app.auth.google import email_allowed, oauth
from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import Course, User, course_instructors
from app.schemas.catalog import MeOut, RoleOut

router = APIRouter(tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.cookie_name,
        token,
        max_age=s.jwt_ttl_minutes * 60,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
    )


@router.get("/auth/google/login")
async def google_login(request: Request):
    if "google" not in oauth._registry:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google login is not configured")
    return await oauth.google.authorize_redirect(request, str(request.url_for("google_callback")))


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(request: Request, session: DbSession):
    frontend = get_settings().frontend_url
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse(f"{frontend}/login?error=google")
    info = token.get("userinfo") or {}
    email, sub = (info.get("email") or "").lower(), info.get("sub")
    if not email or not info.get("email_verified") or not email_allowed(email, info.get("hd")):
        return RedirectResponse(f"{frontend}/login?error=domain")
    user = await session.scalar(
        sa.select(User).where(sa.or_(User.google_sub == sub, sa.func.lower(User.email) == email))
    )
    if user is None or not user.is_active:
        return RedirectResponse(f"{frontend}/login?error=not_provisioned")
    user.google_sub = sub
    await session.commit()
    response = RedirectResponse(frontend)
    _set_cookie(response, jwt.issue(user.id))
    return response


class DevLogin(BaseModel):
    email: EmailStr


@router.post("/auth/dev-login", summary="Log in without Google (dev/test only)")
async def dev_login(body: DevLogin, response: Response, session: DbSession) -> dict:
    s = get_settings()
    if s.env == "prod" or not s.dev_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    user = await session.scalar(sa.select(User).where(sa.func.lower(User.email) == body.email.lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    token = jwt.issue(user.id)
    _set_cookie(response, token)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name)


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentUser, session: DbSession) -> MeOut:
    led = list(await session.scalars(sa.select(Course.id).where(Course.lead_id == principal.id)))
    taught = list(
        await session.scalars(
            sa.select(course_instructors.c.course_id).where(course_instructors.c.user_id == principal.id)
        )
    )
    u = principal.user
    return MeOut(
        id=u.id,
        email=u.email,
        name=u.name,
        title=u.title,
        roles=[RoleOut.model_validate(r) for r in u.roles],
        led_course_ids=led,
        taught_course_ids=[c for c in taught if c not in led],
    )
