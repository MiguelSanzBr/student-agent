import os

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.obs.metrics import render

router = APIRouter()


@router.get("/metrics")
async def metrics() -> Response:
    body = render(os.environ.get("PROMETHEUS_MULTIPROC_DIR"))
    return Response(body, headers={"Content-Type": CONTENT_TYPE_LATEST})
