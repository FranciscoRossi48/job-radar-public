from datetime import date, timedelta


def test_rotation_covers_all_queries_in_three_days(cfg):
    s = cfg.searches
    day = date(2026, 11, 3)
    days = [s.queries_for(day + timedelta(days=i)) for i in range(3)]
    assert all(len(d) == 3 and d[0] == "data engineer" for d in days)
    assert {q for d in days for q in d[1:]} == set(s.rotating_queries)
    assert s.queries_for(day + timedelta(days=3)) == days[0]


def test_no_rotation_when_pool_is_empty(cfg):
    s = cfg.searches.model_copy(update={"rotating_queries": []})  # no tocar el cfg de la sesión
    assert s.queries_for(date(2026, 11, 3)) == s.queries
