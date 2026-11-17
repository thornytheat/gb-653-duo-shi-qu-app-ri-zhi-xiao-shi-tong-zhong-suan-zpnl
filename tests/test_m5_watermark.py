from app.modules.m5_watermark.core import advance, run
def test_trap_1(): # TRAP-11
    assert advance("2026-11-15T01:00:00Z",["2026-11-15T00:40:00Z"])["new"].startswith("2026-11-15T01:00:00")
def test_trap_2(): assert advance("2026-11-15T01:00:00Z",["2026-11-15T00:40:00Z"])["late"]==["2026-11-15T00:40:00Z"]
def test_advances_after_window(): assert advance("2026-11-15T01:00:00Z",["2026-11-15T01:30:00Z"])["advanced"] is True
def test_exact_window_no_advance(): assert advance("2026-11-15T01:00:00Z",["2026-11-15T01:15:00Z"])["advanced"] is False
def test_empty_stable(): assert advance("2026-11-15T01:00:00Z",[])["advanced"] is False
def test_custom_window(): assert advance("2026-11-15T01:00:00Z",["2026-11-15T01:06:00Z"],5)["advanced"] is True
def test_sample_late(): assert run({})["late_count"]==1
def test_all_events_kept(): assert run({})["valid_count"]==2
