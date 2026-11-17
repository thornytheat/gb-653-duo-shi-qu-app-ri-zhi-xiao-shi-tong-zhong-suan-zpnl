from datetime import datetime, timedelta, timezone
SAMPLE={"current":"2026-11-15T01:00:00Z","events":["2026-11-15T01:05:00Z","2026-11-15T00:40:00Z"],"minutes":15}
def parse(value): return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(timezone.utc)
def advance(current, events, minutes=15):
    old=parse(current); newest=max([old]+[parse(x) for x in events]); proposed=newest-timedelta(minutes=minutes); new=max(old,proposed)
    return {"old":old.isoformat(),"new":new.isoformat(),"late":[x for x in events if parse(x)<old],"advanced":new>old}
def run(payload):
    d=SAMPLE|payload; out=advance(d["current"],d["events"],d.get("minutes",15)); return {"status":"READY","input_count":len(d["events"]),"valid_count":len(d["events"]),"quarantine_count":0,"late_count":len(out["late"]),**out,"reason":"LATE" if out["late"] else "OK"}
