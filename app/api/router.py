from fastapi import APIRouter

from app.api import (
    analytics,
    approval,
    auth,
    catalog,
    improve,
    notifications,
    precheck,
    sessions,
    syllabi,
    wizard,
)

api_router = APIRouter()
for module in (
    auth,
    catalog,
    syllabi,
    approval,
    improve,
    precheck,
    wizard,
    sessions,
    analytics,
    notifications,
):
    api_router.include_router(module.router)
