"""Google OpenID Connect login. Users must already exist (provisioned with roles); Google only proves identity."""

from authlib.integrations.starlette_client import OAuth

from app.config import get_settings

oauth = OAuth()


def register() -> None:
    s = get_settings()
    if not s.google_client_id or "google" in oauth._registry:
        return
    oauth.register(
        name="google",
        client_id=s.google_client_id,
        client_secret=s.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def email_allowed(email: str, hosted_domain: str | None) -> bool:
    domain = get_settings().google_allowed_domain
    if not domain:
        return True
    return hosted_domain == domain and email.lower().endswith("@" + domain.lower())
