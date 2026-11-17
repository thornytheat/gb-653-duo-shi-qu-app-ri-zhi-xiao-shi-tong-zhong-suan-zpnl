import pytest
from app.modules.m2_time.core import normalize, run
def test_trap_1(): # TRAP-05, TRAP-14
    assert normalize("2026-11-15T09:00:00+08:00")=="2026-11-15T01:00:00Z"
def test_trap_2(): assert normalize("2026-11-01T01:30:00","America/New_York",1)=="2026-11-01T06:30:00Z"
def test_same_instant(): assert normalize("2026-11-15T01:00:00Z")==normalize("2026-11-15T09:00:00+08:00")
def test_naive_needs_zone():
    with pytest.raises(ValueError): normalize("2026-01-01T00:00:00")
def test_dst_gap_rejected():
    with pytest.raises(ValueError): normalize("2026-03-08T02:30:00","America/New_York")
def test_utc_suffix(): assert normalize("2026-01-01T00:00:00+00:00").endswith("Z")
def test_sample_count(): assert run({})["valid_count"]==3
def test_bad_count(): assert run({"values":[{"value":"2026-01-01T00:00:00"}]})["quarantine_count"]==1
