from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M2-时间解析"])

@router.get("/time")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m2_time.html", {"title": "时间解析", "data": data})

@router.post("/api/time/normalize")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
