#!/usr/bin/env python3
"""Simulate schema drift on the E2E_Retail_Analytics demo dataset.

Schema drift is, by definition, "the schema changed since it was last
scanned" — that can't be part of the idempotent boot-time seed
(app.core.e2e_seed_data), so it's a separate, on-demand script instead.
Run it, then re-scan the connection in Schema Intel (or hit
POST /api/v1/schema/{id}/rescan) to see the drift detected:

    docker exec dataone-api python3 scripts/simulate_e2e_drift.py

Applies three realistic, common schema-evolution changes in one pass:
  1. Adds a new table (analytics_returns) — a whole new feature shipped.
  2. Adds a new column (analytics_customers.loyalty_tier) — a field added
     to an existing table.
  3. Renames a column (analytics_products.category ->
     analytics_products.product_category) — drift detection can't know
     it's a rename, so this shows up as one column removed + one added,
     which is itself a realistic thing to verify the UI communicates
     clearly rather than just listing as two unrelated changes.

Idempotent: safe to run more than once — each change is skipped if
already applied.
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_PATH = "/shared/data/dataplane_e2e_retail.db"


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    return row is not None


def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def simulate_drift(path: str) -> None:
    if not os.path.exists(path):
        print(f"error: {path} does not exist — run the app once first so the "
              f"E2E_Retail_Analytics dataset gets seeded (see app.core.e2e_seed_data).",
              file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(path)
    cur = conn.cursor()

    if not _table_exists(cur, "analytics_returns"):
        cur.execute("""
            CREATE TABLE analytics_returns (
                return_id INTEGER PRIMARY KEY, order_id INTEGER,
                reason TEXT, refund_amount REAL, returned_at TIMESTAMP
            )
        """)
        print("+ added table analytics_returns")
    else:
        print("= table analytics_returns already exists, skipped")

    if not _column_exists(cur, "analytics_customers", "loyalty_tier"):
        cur.execute("ALTER TABLE analytics_customers ADD COLUMN loyalty_tier TEXT")
        print("+ added column analytics_customers.loyalty_tier")
    else:
        print("= column analytics_customers.loyalty_tier already exists, skipped")

    if _column_exists(cur, "analytics_products", "category") and not _column_exists(
        cur, "analytics_products", "product_category",
    ):
        cur.execute("ALTER TABLE analytics_products RENAME COLUMN category TO product_category")
        print("~ renamed analytics_products.category -> analytics_products.product_category")
    else:
        print("= analytics_products.category already renamed (or missing), skipped")

    conn.commit()
    conn.close()
    print("\nDone. Re-scan the E2E_Retail_Analytics connection "
          "(POST /api/v1/schema/{connection_id}/rescan or the Schema Intel UI's "
          "'Re-scan' button) to see this drift detected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default=DEFAULT_PATH, help="Path to the E2E retail SQLite file")
    args = parser.parse_args()
    simulate_drift(args.path)
