from datetime import datetime
SAMPLE={"events":[{"timestamp":"2026-11-15T01:00:00Z","service":"pay","status":200},{"timestamp":"2026-11-15T01:59:59Z","service":"pay","status":500},{"timestamp":"2026-11-15T02:00:00Z","service":"pay","status":200}]}
def hour_key(value):
    dt=datetime.fromisoformat(value.replace("Z","+00:00")); return dt.strftime("%Y-%m-%dT%H:00:00Z")
def bucket(events):
    out={}
    for row in events:
        key=(hour_key(row["timestamp"]),row["service"]); item=out.setdefault(key,{"hour":key[0],"service":key[1],"count":0,"errors":0}); item["count"]+=1; item["errors"]+=int(int(row["status"])>=500)
    return sorted(out.values(),key=lambda x:(x["hour"],x["service"]))
def run(payload):
    events=payload.get("events",SAMPLE["events"]); out=bucket(events); return {"status":"READY","input_count":len(events),"valid_count":sum(x["count"] for x in out),"quarantine_count":0,"buckets":len(out),"errors":sum(x["errors"] for x in out),"reason":"OK"}
