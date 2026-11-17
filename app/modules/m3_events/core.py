SAMPLE={"events":[{"request_id":"r1","status":200,"timestamp":"2026-11-15T01:00:00Z"},{"request_id":"r1","status":200,"timestamp":"2026-11-15T01:00:00Z"},{"request_id":"r2","status":500,"timestamp":"2026-11-15T01:01:00Z"},{"request_id":"r2","status":200,"timestamp":"2026-11-15T01:02:00Z"}]}
def dedupe(events):
    seen={}; duplicate=[]; conflict=[]
    for row in events:
        key=str(row["request_id"])
        if key not in seen: seen[key]=row; continue
        if seen[key]==row: duplicate.append(row)
        else: conflict.append({"request_id":key,"first":seen[key],"other":row,"reason":"DUPLICATE_CONFLICT"})
    return {"accepted":list(seen.values()),"duplicate":duplicate,"conflict":conflict}
def run(payload):
    events=payload.get("events",SAMPLE["events"]); out=dedupe(events); return {"status":"READY","input_count":len(events),"valid_count":len(out["accepted"]),"quarantine_count":len(out["conflict"]),"duplicates":len(out["duplicate"]),"reason":"CONFLICT" if out["conflict"] else "OK"}
