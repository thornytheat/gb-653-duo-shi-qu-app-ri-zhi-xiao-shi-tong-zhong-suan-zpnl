import json
SAMPLE={"text":'{"request_id":"9007199254740993","timestamp":"2026-11-15T01:05:00Z","service":"pay","status":200}\n{"request_id":"r2","timestamp":"2026-11-15T09:10:00+08:00","service":"pay","status":500}\n{"request_id":"r3"'}
def parse(text):
    rows=[]; quarantine=[]
    for line_no,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        try:
            row=json.loads(line); row["request_id"]=str(row.get("request_id","")); row["source_line"]=line_no; rows.append(row)
        except json.JSONDecodeError: quarantine.append({"source_line":line_no,"reason":"TRUNCATED","raw":line})
    return {"rows":rows,"quarantine":quarantine}
def run(payload):
    out=parse(payload.get("text",SAMPLE["text"])); return {"status":"READY","input_count":len(out["rows"])+len(out["quarantine"]),"valid_count":len(out["rows"]),"quarantine_count":len(out["quarantine"]),"first_id":out["rows"][0]["request_id"],"reason":out["quarantine"][0]["reason"] if out["quarantine"] else "OK"}
