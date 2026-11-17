from app.modules.m1_logs.core import SAMPLE, parse, run
def test_trap_1(): # TRAP-03, TRAP-13
    assert parse('{"request_id":"1"}\n{"request_id":')["quarantine"][0]["reason"]=="TRUNCATED"
def test_trap_2(): assert parse('{"request_id":"9007199254740993"}')["rows"][0]["request_id"]=="9007199254740993"
def test_blank_skipped(): assert parse('\n{"request_id":"a"}')["rows"][0]["source_line"]==2
def test_source_line(): assert parse('{"request_id":"a"}')["rows"][0]["source_line"]==1
def test_empty_id_string(): assert parse('{}')["rows"][0]["request_id"]==""
def test_sample_valid(): assert run(SAMPLE)["valid_count"]==2
def test_sample_bad(): assert run(SAMPLE)["quarantine_count"]==1
def test_accounting():
    out=run(SAMPLE); assert out["input_count"]==out["valid_count"]+out["quarantine_count"]
