from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M3-事件去重"])

@router.get("/events")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m3_events.html", {"title": "事件去重", "data": data})

@router.post("/api/events/dedupe")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
