from fastapi import APIRouter, Body, Request
from .service import execute
router = APIRouter(tags=["M4-小时分桶"])

@router.get("/buckets")
def page(request: Request):
    data = execute()
    return request.app.state.templates.TemplateResponse(request, "m4_buckets.html", {"title": "小时分桶", "data": data})

@router.post("/api/buckets/rebuild")
def api(payload: dict = Body(default={})):
    return execute(payload or None)
