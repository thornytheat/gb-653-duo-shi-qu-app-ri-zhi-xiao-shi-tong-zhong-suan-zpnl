from datetime import datetime, timezone
from zoneinfo import ZoneInfo
SAMPLE={"values":[{"value":"2026-11-15T09:00:00+08:00"},{"value":"2026-11-15T01:00:00Z"},{"value":"2026-11-01T01:30:00","zone":"America/New_York","fold":1}]}
def normalize(value, zone=None, fold=0):
    raw=value.replace("Z","+00:00"); dt=datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        if not zone: raise ValueError("TIMEZONE_REQUIRED")
        dt=dt.replace(tzinfo=ZoneInfo(zone),fold=int(fold))
        back=dt.astimezone(timezone.utc).astimezone(ZoneInfo(zone))
        if back.replace(tzinfo=None)!=datetime.fromisoformat(raw): raise ValueError("DST_GAP")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")
def run(payload):
    values=payload.get("values",SAMPLE["values"]); out=[]; bad=0
    for item in values:
        try: out.append(normalize(**item))
        except ValueError: bad+=1
    return {"status":"READY","input_count":len(values),"valid_count":len(out),"quarantine_count":bad,"utc":out,"reason":"OK" if not bad else "TIME_ERROR"}
