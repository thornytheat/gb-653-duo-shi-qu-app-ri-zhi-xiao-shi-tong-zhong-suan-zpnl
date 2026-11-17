from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M5-迟到修订"])

@router.get("/watermark")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m5_watermark.html", {"title": "迟到修订", "data": data})

@router.post("/api/watermark/advance")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
