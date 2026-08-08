"""Backward-compatible import location for the aggregate v2 HTTP router."""

from app.v2.api.routes import router

__all__ = ["router"]
