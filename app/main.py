from fastapi import FastAPI

from app.api import ROUTERS

app = FastAPI(title="student-agent")
for _router in ROUTERS:
    app.include_router(_router)
