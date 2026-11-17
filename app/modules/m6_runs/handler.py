from fastapi import APIRouter, Request
from fastapi.responses import Response
from .service import execute
router = APIRouter(tags=["M6-批次对账"])

@router.get("/runs")
def page(request: Request):
    data = execute({"store": request.app.state.store})
    return request.app.state.templates.TemplateResponse(request, "m6_runs.html", {"title": "批次对账", "data": data})

@router.get("/api/runs")
def runs(request: Request):
    execute({"store": request.app.state.store})
    return {"runs": request.app.state.store.all()}

@router.get("/api/export/buckets.csv")
def export(request: Request):
    data = execute({"store": request.app.state.store})
    return Response(data["csv"], media_type="text/csv", headers={"Content-Disposition": "attachment; filename=evidence.csv"})
