from Data.tools.check_benchmark import check


def _baseline():
    return {
        'scenario': 'offline-fixture-astrub-fixed-coordinate',
        'recognized_key': 'area:18',
        'sqlite_coordinate_queries': 0,
        'location': {'count': 80, 'wall_p95_ms': 0.1},
        'hud_extract': {'count': 20, 'wall_p95_ms': 2.5},
    }


def test_benchmark_guard_accepts_current_smoke_metrics():
    assert check(_baseline()) == []


def test_benchmark_guard_blocks_pathological_performance_and_queries():
    result = _baseline()
    result['sqlite_coordinate_queries'] = 400
    result['hud_extract']['wall_p95_ms'] = 200
    result['ocr'] = {'zone': {'last_error': 'bad'}}
    problems = check(result)
    assert len(problems) >= 3
