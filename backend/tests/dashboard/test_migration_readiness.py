from app.services.dashboard_service import calculate_migration_readiness


def test_readiness_uses_documented_weights():
    assert calculate_migration_readiness(80, 2, 10, 1) == (82, 80, 90)


def test_readiness_bounds_inputs_and_treats_no_runs_as_neutral():
    assert calculate_migration_readiness(140, 20, 0, 4) == (70, 0, 100)


def test_readiness_never_counts_more_failures_than_completed_runs():
    assert calculate_migration_readiness(100, 0, 2, 5) == (80, 100, 0)
