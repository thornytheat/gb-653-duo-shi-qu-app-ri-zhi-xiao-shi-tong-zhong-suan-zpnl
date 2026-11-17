import csv, io
from app.manifest import canonical_digest, run_id
SAMPLE={"store":None,"rows":[{"hour":"2026-11-15T01:00:00Z","service":"pay","count":2,"errors":1},{"hour":"2026-11-15T02:00:00Z","service":"pay","count":1,"errors":0}]}
def csv_text(rows):
    out=io.StringIO(); w=csv.DictWriter(out,fieldnames=["hour","service","count","errors"],lineterminator="\n"); w.writeheader(); w.writerows(rows); return out.getvalue()
def run(payload):
    rows=payload.get("rows",SAMPLE["rows"]); digest=canonical_digest(rows); rid=run_id(rows); result={"input_count":sum(x["count"] for x in rows),"valid_count":sum(x["count"] for x in rows),"quarantine_count":0,"errors":sum(x["errors"] for x in rows)}; store=payload.get("store")
    if store: rid=store.record(digest,rid,result)["run_id"]
    return {"status":"READY","run_id":rid,"digest":digest,"csv":csv_text(rows),**result,"reason":"OK"}
