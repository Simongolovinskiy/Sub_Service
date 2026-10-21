"""API v1 HTTP routers."""
from fastapi import APIRouter

from app.application.api.v1.http.controllers.products import router as products_router
from app.application.api.v1.http.controllers.health import router as health_router

api_v1_router = APIRouter()

# Include sub-routers
api_v1_router.include_router(health_router, tags=["health"])
api_v1_router.include_router(products_router, prefix="/products", tags=["products"])
