from pixelrelay.distribution import Contributor, PhotoSlot, distribute


def people(*specs):
    return [Contributor(f"m{i}", f"P{i}", i, opted, cap, cur) for i, (opted, cap, cur) in enumerate(specs)]


def photos(n, **kw):
    return [PhotoSlot(f"p{i:02d}", order=i, **kw) for i in range(n)]


def test_even_split():
    plan = distribute(photos(9), people((True, None, 0), (True, None, 0), (True, None, 0)))
    assert sorted(plan.load.values()) == [3, 3, 3]
    assert plan.unassigned == []


def test_respects_capacity_and_reports_leftovers():
    plan = distribute(photos(6), people((True, 1, 0), (True, 2, 0)))
    assert plan.load == {"m0": 1, "m1": 2}
    assert len(plan.unassigned) == 3


def test_skips_people_not_opted_in():
    plan = distribute(photos(4), people((False, None, 0), (True, None, 0)))
    assert {m for _, m in plan.assignments} == {"m1"}


def test_counts_existing_assignments():
    plan = distribute(photos(4), people((True, None, 3), (True, None, 0)))
    assert plan.load == {"m0": 4, "m1": 3}


def test_existing_load_counts_against_capacity():
    plan = distribute(photos(3), people((True, 2, 2), (True, None, 0)))
    assert {m for _, m in plan.assignments} == {"m1"}


def test_priority_first():
    ps = [PhotoSlot("low", 0, 0), PhotoSlot("high", 5, 1)]
    plan = distribute(ps, people((True, 1, 0)))
    assert plan.assignments == [("high", "m0")]
    assert plan.unassigned == ["low"]


def test_deterministic():
    a = distribute(photos(11), people((True, None, 0), (True, 3, 0), (True, None, 1)))
    b = distribute(photos(11), people((True, None, 0), (True, 3, 0), (True, None, 1)))
    assert a.assignments == b.assignments


def test_nobody_available():
    plan = distribute(photos(2), people((False, None, 0)))
    assert plan.assignments == [] and len(plan.unassigned) == 2
