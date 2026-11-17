from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M1-日志采信"])

@router.get("/logs")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m1_logs.html", {"title": "日志采信", "data": data})

@router.post("/api/logs/import")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
