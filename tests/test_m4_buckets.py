from app.modules.m4_buckets.core import hour_key, bucket, run
def test_trap_1(): # TRAP-09, TRAP-23
    assert hour_key("2026-11-15T01:59:59Z")=="2026-11-15T01:00:00Z"
def test_trap_2(): assert hour_key("2026-11-15T02:00:00Z")=="2026-11-15T02:00:00Z"
def test_two_buckets(): assert len(bucket([{"timestamp":"2026-11-15T01:00:00Z","service":"a","status":200},{"timestamp":"2026-11-15T01:00:00Z","service":"b","status":200}]))==2
def test_error_count(): assert bucket([{"timestamp":"2026-11-15T01:00:00Z","service":"a","status":500}])[0]["errors"]==1
def test_non_error_count(): assert bucket([{"timestamp":"2026-11-15T01:00:00Z","service":"a","status":499}])[0]["errors"]==0
def test_count_sum(): assert sum(x["count"] for x in bucket(run.__globals__["SAMPLE"]["events"]))==3
def test_run_buckets(): assert run({})["buckets"]==2
def test_run_conserves(): assert run({})["input_count"]==run({})["valid_count"]
