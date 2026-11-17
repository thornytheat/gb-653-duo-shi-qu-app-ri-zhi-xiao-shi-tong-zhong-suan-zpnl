from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from app.errors import DomainError
from .service import EvidenceStore, _PartialFailure

router = APIRouter(tags=["M7-补证裁决"])


def _store(request: Request) -> EvidenceStore:
    store = getattr(request.app.state, "evidence", None)
    if store is None:
        store = EvidenceStore(request.app.state.settings.db_path)
        request.app.state.evidence = store
    return store


@router.post("/api/evidence/intake")
def intake(request: Request, payload: dict = Body(default={})):
    """接入真实坏行：给定原文或样例名，解析有效/隔离行并保存批次快照。"""
    text = payload.get("text")
    sample = payload.get("sample")
    if text is None and sample is None:
        sample = "dirty"
    if text is not None and not isinstance(text, str):
        raise DomainError("INVALID_VALUE", "text 必须是字符串")
    parsed = _store(request).intake(
        text, sample, request.app.state.settings.rule_version
    )
    return {"status": "READY", **parsed}


@router.get("/api/evidence/batch")
def batch(request: Request):
    return _store(request).overview()


@router.get("/api/evidence/originals/{original_id}")
def original(request: Request, original_id: str):
    return _store(request).original_detail(original_id)


@router.post("/api/evidence/candidates")
def candidates(request: Request, payload: dict = Body(default={})):
    return _store(request).add_candidates(payload or {})


@router.post("/api/evidence/adjudicate")
def adjudicate(request: Request, payload: dict = Body(default={})):
    try:
        return _store(request).adjudicate(payload or {})
    except _PartialFailure as exc:
        return JSONResponse(
            status_code=422,
            content={"error": exc.code, "message": exc.message, **exc.info},
        )


@router.post("/api/evidence/revoke")
def revoke(request: Request, payload: dict = Body(default={})):
    return _store(request).revoke(payload or {})


@router.post("/api/evidence/resume")
def resume(request: Request, payload: dict = Body(default={})):
    return _store(request).resume(payload.get("batch_id"))


@router.get("/api/evidence/buckets")
def buckets(request: Request, batch_id: str | None = Query(default=None)):
    return _store(request).buckets(batch_id)


@router.get("/evidence")
def page(request: Request):
    overview = _store(request).overview()
    data = {
        "status": overview["status"],
        "input_count": overview["counts"]["input_count"],
        "valid_count": overview["counts"]["valid_count"],
        "quarantine_count": overview["counts"]["quarantine_count"],
    }
    return request.app.state.templates.TemplateResponse(
        request, "m7_evidence.html", {"title": "补证裁决", "data": data}
    )
