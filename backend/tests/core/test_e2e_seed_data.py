import sqlite3

from app.core.e2e_seed_data import (
    seed_e2e_retail_analytics,
    seed_e2e_retail_warehouse,
)


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_e2e_source_and_target_cover_executable_pipeline_shapes(tmp_path):
    source = seed_e2e_retail_analytics(str(tmp_path))
    target = seed_e2e_retail_warehouse(str(tmp_path))

    assert _tables(source) == {
        "analytics_customers",
        "analytics_products",
        "analytics_orders",
        "analytics_support_tickets",
    }
    assert _tables(target) == {
        "dim_customer",
        "dim_product",
        "fact_order",
        "fact_support_ticket",
        "order_status_snapshot",
    }

    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analytics_orders").fetchone()[0] == 1500
        assert conn.execute(
            "SELECT COUNT(*) FROM analytics_customers WHERE phone IS NULL"
        ).fetchone()[0] > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM analytics_customers "
            "GROUP BY email HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone() is not None

    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fact_order").fetchone()[0] == 0
        pk_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(fact_order)") if row[5]
        }
        assert pk_columns == {"order_key"}
        assert not any(row[5] for row in conn.execute(
            "PRAGMA table_info(order_status_snapshot)"
        ))


def test_e2e_seed_is_idempotent(tmp_path):
    first_source = seed_e2e_retail_analytics(str(tmp_path))
    first_target = seed_e2e_retail_warehouse(str(tmp_path))

    assert seed_e2e_retail_analytics(str(tmp_path)) == first_source
    assert seed_e2e_retail_warehouse(str(tmp_path)) == first_target

    with sqlite3.connect(first_source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM analytics_orders").fetchone()[0] == 1500
