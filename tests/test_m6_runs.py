from app.modules.m6_runs.core import SAMPLE, csv_text, run
class Store:
    def __init__(self): self.x=None
    def record(self,d,r,v): self.x=self.x or (d,r,v); return {"run_id":self.x[1],"result":self.x[2]}
def test_csv_header(): assert csv_text(SAMPLE["rows"]).startswith("hour,service,count,errors")
def test_csv_utc(): assert "2026-11-15T01:00:00Z" in csv_text(SAMPLE["rows"])
def test_counts(): assert run({})["input_count"]==3
def test_errors(): assert run({})["errors"]==1
def test_trap_1(): # TRAP-12
    s=Store(); assert run({"store":s})["run_id"]==run({"store":s})["run_id"]
def test_trap_2(): assert run({})["digest"]==run({})["digest"]
def test_valid_conserves(): assert run({})["input_count"]==run({})["valid_count"]
def test_no_quarantine(): assert run({})["quarantine_count"]==0
