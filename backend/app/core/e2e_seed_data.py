"""Generates the 'E2E_Retail_Analytics' demo dataset — a purpose-built,
deliberately messy fact/dimension schema for end-to-end testing (see
docs/E2E_TESTING_GUIDE.md).

Unlike the other seeded demo connections (small, clean, hand-written rows —
good for a quick smoke test), this one is sized and shaped to exercise
things the other seed data can't:

- Volume: ~1500 fact rows, enough to meaningfully test pagination
  (Query Studio), batch pipeline runs, and non-trivial Visualize
  aggregations.
- PII pattern variety: multiple email/phone/SSN formats (including some
  deliberately malformed), plus synthetic credit-card-shaped values — so
  Schema Intel's content-based classification has real signal instead of
  5-8 near-identical rows.
- Messiness: nulls, duplicate emails, orphaned foreign keys, inconsistent
  casing on status/payment-method strings — realistic source-system grime
  for validation, AI-matching-confidence, and mapping edge cases to react
  to.
- Schema drift: paired with scripts/simulate_e2e_drift.py, which mutates
  this dataset's schema on demand (it can't be part of this idempotent
  seed — drift is by definition "changed since last scan").

Generation is deterministic (fixed random seed) so re-seeding after a
fresh ``docker compose down -v`` produces byte-identical data every time.
"""
from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta

_SEED = 20260713  # fixed — deterministic generation, not a security secret

_FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
    "William", "Elizabeth", "David", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Nancy", "Matthew", "Lisa",
    "Anthony", "Betty", "Mark", "Margaret", "Priya", "Wei", "Fatima", "Diego",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
    "Nguyen", "Kim", "Khan", "Silva", "Muller", "Rossi",
]
_EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "corpmail.io", "acmeco.com", "hotmail.com"]
_REGIONS = ["North America", "EMEA", "APAC", "LATAM"]
_SEGMENTS = ["Enterprise", "SMB", "Consumer", "Startup"]
_CATEGORIES = ["Electronics", "Home & Garden", "Apparel", "Sports", "Books", "Toys", "Beauty", "Automotive"]
# Deliberately inconsistent casing/spelling — realistic source-system grime,
# not a bug in the generator.
_STATUS_VARIANTS = [
    "completed", "Completed", "COMPLETED", "cancelled", "Cancelled",
    "pending", "Pending", "refunded", "Refunded",
]
_PAYMENT_VARIANTS = [
    "Credit Card", "credit_card", "CREDIT CARD", "PayPal", "paypal",
    "Bank Transfer", "bank_transfer", "Debit Card",
]
_PRIORITY_VARIANTS = ["Low", "Medium", "High", "Urgent", "low", "high"]
_TICKET_SUBJECTS = [
    "Order not received", "Refund request", "Wrong item shipped", "Billing question",
    "Cannot log in to account", "Product defect", "Shipping address change",
    "Discount code not working", "Duplicate charge", "Cancel subscription",
    "Product recommendation", "Return label needed", "Late delivery",
    "Account locked", "General inquiry",
]


def _fake_email(first: str, last: str, rng: random.Random) -> str:
    domain = rng.choice(_EMAIL_DOMAINS)
    style = rng.random()
    if style < 0.55:
        return f"{first.lower()}.{last.lower()}@{domain}"
    if style < 0.7:
        return f"{first[0].lower()}{last.lower()}@{domain}"
    if style < 0.82:
        return f"{first.lower()}.{last.lower()}@{domain}".upper()
    if style < 0.92:
        tag = rng.randint(1, 99)
        return f"{first.lower()}.{last.lower()}+{tag}@{domain}"
    # Deliberately malformed — missing '@' — messy-data edge case.
    return f"{first.lower()}.{last.lower()}{domain}"


def _fake_phone(rng: random.Random) -> str | None:
    if rng.random() < 0.1:
        return None
    n = [rng.randint(0, 9) for _ in range(10)]
    style = rng.random()
    if style < 0.35:
        return f"({n[0]}{n[1]}{n[2]}) {n[3]}{n[4]}{n[5]}-{n[6]}{n[7]}{n[8]}{n[9]}"
    if style < 0.65:
        return f"{n[0]}{n[1]}{n[2]}-{n[3]}{n[4]}{n[5]}-{n[6]}{n[7]}{n[8]}{n[9]}"
    if style < 0.85:
        return f"+1.{n[0]}{n[1]}{n[2]}.{n[3]}{n[4]}{n[5]}.{n[6]}{n[7]}{n[8]}{n[9]}"
    return "".join(str(d) for d in n)


def _fake_ssn(rng: random.Random) -> str | None:
    if rng.random() < 0.3:
        return None
    # Area numbers 900-999 were never issued as real SSNs — safe synthetic
    # fixture data, but still matches the app's SSN content-pattern regex.
    area = rng.randint(900, 999)
    group = rng.randint(1, 99)
    serial = rng.randint(1, 9999)
    return f"{area}-{group:02d}-{serial:04d}"


def _fake_credit_card(rng: random.Random) -> str | None:
    if rng.random() < 0.5:
        return None
    # Synthetic 16-digit values only (Visa test-range prefix "4") — never
    # real cardholder data, purely to exercise the content-pattern classifier.
    digits = [4] + [rng.randint(0, 9) for _ in range(15)]
    s = "".join(str(d) for d in digits)
    return f"{s[0:4]}-{s[4:8]}-{s[8:12]}-{s[12:16]}"


def seed_e2e_retail_analytics(data_dir: str) -> str:
    """Idempotently create the E2E_Retail_Analytics physical SQLite file.
    Returns its path. No-op if the file already exists (matches the
    pattern of every other seeded demo database in app.main's lifespan)."""
    path = os.path.join(data_dir, "dataplane_e2e_retail.db")
    if os.path.exists(path):
        return path

    rng = random.Random(_SEED)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE analytics_customers (
            customer_id INTEGER PRIMARY KEY, full_name TEXT, email TEXT,
            phone TEXT, ssn TEXT, region TEXT, segment TEXT,
            signup_date TIMESTAMP, is_active INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE analytics_products (
            product_id INTEGER PRIMARY KEY, sku TEXT, name TEXT,
            category TEXT, unit_price REAL, launched_at DATE
        )
    """)
    c.execute("""
        CREATE TABLE analytics_orders (
            order_id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER,
            quantity INTEGER, unit_price REAL, discount_pct REAL,
            order_date TIMESTAMP, status TEXT, payment_method TEXT,
            credit_card_number TEXT
        )
    """)
    c.execute("""
        CREATE TABLE analytics_support_tickets (
            ticket_id INTEGER PRIMARY KEY, customer_id INTEGER, subject TEXT,
            description TEXT, created_at TIMESTAMP, resolved_at TIMESTAMP,
            priority TEXT, agent_email TEXT
        )
    """)

    # ── Customers (300) ─────────────────────────────────────────────
    # A handful of duplicate emails simulate repeat/duplicate signups —
    # a real messy-data pattern for entity-resolution-style testing.
    customers = []
    duplicate_email_pool: list[str] = []
    signup_start = datetime(2023, 1, 1)
    for cid in range(1, 301):
        first, last = rng.choice(_FIRST_NAMES), rng.choice(_LAST_NAMES)
        if duplicate_email_pool and rng.random() < 0.04:
            email = rng.choice(duplicate_email_pool)
        else:
            email = _fake_email(first, last, rng)
            if rng.random() < 0.1:
                duplicate_email_pool.append(email)
        signup_date = signup_start + timedelta(days=rng.randint(0, 900))
        customers.append((
            cid, f"{first} {last}", email, _fake_phone(rng), _fake_ssn(rng),
            rng.choice(_REGIONS), rng.choice(_SEGMENTS),
            signup_date.strftime("%Y-%m-%d %H:%M:%S"),
            1 if rng.random() > 0.08 else 0,
        ))
    c.executemany("INSERT INTO analytics_customers VALUES (?,?,?,?,?,?,?,?,?)", customers)

    # ── Products (40) ────────────────────────────────────────────────
    products = []
    for pid in range(1, 41):
        category = rng.choice(_CATEGORIES)
        unit_price = round(rng.uniform(5.0, 500.0), 2)
        launched = datetime(2022, 1, 1) + timedelta(days=rng.randint(0, 1200))
        products.append((
            pid, f"SKU-{1000 + pid}", f"{category} Item #{pid}", category,
            unit_price, launched.strftime("%Y-%m-%d"),
        ))
    c.executemany("INSERT INTO analytics_products VALUES (?,?,?,?,?,?)", products)

    # ── Orders (1500, fact table) ────────────────────────────────────
    # ~3% reference a customer_id/product_id outside the seeded ranges —
    # deliberate orphaned foreign keys (SQLite doesn't enforce FKs by
    # default, so this is safe to seed and won't break anything; it's
    # realistic grime any real source system accumulates).
    order_start = datetime(2024, 1, 1)
    orders = []
    for oid in range(1, 1501):
        customer_id = rng.randint(301, 320) if rng.random() < 0.03 else rng.randint(1, 300)
        product_id = rng.randint(41, 55) if rng.random() < 0.03 else rng.randint(1, 40)
        product_price = next((p[4] for p in products if p[0] == product_id), round(rng.uniform(5.0, 500.0), 2))
        quantity = rng.randint(1, 8)
        discount_pct = None if rng.random() < 0.15 else rng.choice([0, 5, 10, 15, 20, 25])
        order_date = order_start + timedelta(
            days=rng.randint(0, 550), hours=rng.randint(0, 23), minutes=rng.randint(0, 59),
        )
        orders.append((
            oid, customer_id, product_id, quantity, product_price, discount_pct,
            order_date.strftime("%Y-%m-%d %H:%M:%S"),
            rng.choice(_STATUS_VARIANTS), rng.choice(_PAYMENT_VARIANTS),
            _fake_credit_card(rng),
        ))
    c.executemany("INSERT INTO analytics_orders VALUES (?,?,?,?,?,?,?,?,?,?)", orders)

    # ── Support tickets (150) ────────────────────────────────────────
    ticket_start = datetime(2024, 3, 1)
    tickets = []
    for tid in range(1, 151):
        customer_id = rng.randint(1, 300)
        created = ticket_start + timedelta(days=rng.randint(0, 450), hours=rng.randint(0, 23))
        resolved = None if rng.random() < 0.2 else created + timedelta(hours=rng.randint(1, 240))
        subject = rng.choice(_TICKET_SUBJECTS)
        tickets.append((
            tid, customer_id, subject,
            f"Customer reported: {subject.lower()}. Order/account details on file.",
            created.strftime("%Y-%m-%d %H:%M:%S"),
            resolved.strftime("%Y-%m-%d %H:%M:%S") if resolved else None,
            rng.choice(_PRIORITY_VARIANTS),
            f"agent{rng.randint(1, 12)}@dataplane.ai",
        ))
    c.executemany("INSERT INTO analytics_support_tickets VALUES (?,?,?,?,?,?,?,?)", tickets)

    conn.commit()
    conn.close()
    return path


def seed_e2e_retail_warehouse(data_dir: str) -> str:
    """Create the empty warehouse paired with ``E2E_Retail_Analytics``.

    The target deliberately uses warehouse-style table and column names so AI
    matching has real semantic work to do.  Its shape also provides executable
    pipeline scenarios: primary-key upserts, a >1-batch fact load, and a
    no-primary-key full replacement.  Nullable target attributes keep the
    canonical load compatible with the deliberately dirty source rows; stricter
    validation failures can still be demonstrated with ``required_email``.
    """
    path = os.path.join(data_dir, "dataone_e2e_retail_warehouse.db")
    if os.path.exists(path):
        return path

    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE dim_customer (
            customer_key INTEGER PRIMARY KEY, customer_name TEXT,
            email_address TEXT, telephone TEXT, national_id TEXT,
            sales_region TEXT, customer_segment TEXT,
            registration_ts TIMESTAMP, active_flag INTEGER,
            required_email TEXT NOT NULL DEFAULT 'unknown'
        )
    """)
    c.execute("""
        CREATE TABLE dim_product (
            product_key INTEGER PRIMARY KEY, stock_keeping_unit TEXT,
            product_name TEXT, product_category TEXT,
            list_price REAL, launch_date DATE
        )
    """)
    c.execute("""
        CREATE TABLE fact_order (
            order_key INTEGER PRIMARY KEY, customer_key INTEGER,
            product_key INTEGER, item_quantity INTEGER, sale_unit_price REAL,
            discount_percent REAL, ordered_at TIMESTAMP, order_status TEXT,
            tender_type TEXT, payment_card TEXT
        )
    """)
    c.execute("""
        CREATE TABLE fact_support_ticket (
            ticket_key INTEGER PRIMARY KEY, customer_key INTEGER,
            ticket_subject TEXT, ticket_description TEXT,
            opened_at TIMESTAMP, closed_at TIMESTAMP,
            priority_code TEXT, support_agent_email TEXT
        )
    """)
    # No PK by design: mapping this from analytics_orders exercises the
    # executor's governed full-table-replacement branch.
    c.execute("""
        CREATE TABLE order_status_snapshot (
            order_key INTEGER, order_status TEXT, captured_at TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return path
