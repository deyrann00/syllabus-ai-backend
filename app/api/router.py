from fastapi import APIRouter

from app.api import (
    analytics,
    approval,
    catalog,
    improve,
    me,
    notifications,
    precheck,
    sessions,
    syllabi,
    wizard,
)

api_router = APIRouter()
for module in (
    me,
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
