from fastapi import APIRouter

from app.api import a2a, chat, dev, feedback, health, metrics, protocols, web

ROUTERS: tuple[APIRouter, ...] = (
    dev.router, chat.router, protocols.router, feedback.router,
    a2a.router, health.router, metrics.router, web.router,
)  # fmt: skip
