from app.modules.m3_events.core import dedupe, run
def test_trap_1(): # TRAP-07, TRAP-16
    assert len(dedupe([{"request_id":"a","v":1},{"request_id":"a","v":2}])["conflict"])==1
def test_trap_2(): assert len(dedupe([{"request_id":"a"},{"request_id":"a"}])["duplicate"])==1
def test_first_kept(): assert dedupe([{"request_id":"a","v":1},{"request_id":"a","v":2}])["accepted"][0]["v"]==1
def test_large_id(): assert dedupe([{"request_id":"9007199254740993"}])["accepted"][0]["request_id"]=="9007199254740993"
def test_empty(): assert dedupe([])["accepted"]==[]
def test_two_keys(): assert len(dedupe([{"request_id":"a"},{"request_id":"b"}])["accepted"])==2
def test_sample_conflict(): assert run({})["reason"]=="CONFLICT"
def test_input_counts_all(): assert run({})["input_count"]==4
