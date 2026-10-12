"""On-demand demo/sample data.

Not loaded automatically at startup (see `app.main.lifespan`) — any logged-in
user can load or remove it from the dashboard via
`app.api.routers.demo_data`. The physical-file seed helpers below are
idempotent (each skips if its file already exists), so `load_demo_data` is
safe to call repeatedly, including after a partial/interrupted prior run.
"""
import logging
import os
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.connection import DBConnection
from app.services.audit_helper import record_audit

logger = logging.getLogger(__name__)

DEMO_DATA_DIR = "/shared/data"

DEMO_CONNECTION_NAMES = [
    "CRM_Source_Analytics",
    "Data_Warehouse_Target",
    "ECommerce_MySQL",
    "Finance_Oracle",
    "HR_Postgres",
    "E2E_Retail_Analytics",
    "E2E_Retail_Warehouse",
]


# ── Seed helpers (physical sample SQLite databases) ───────────────────────────
# Moved as-is from app.main's former startup seeding step (dataone_demo_data
# opt-in change) — each already guards on os.path.exists, unchanged below.

def seed_crm_source(path: str) -> None:
    """CRM source — accounts, contacts, opportunities, activities, cases.
    
    ~90 rows across 5 tables, simulating a mid-market B2B CRM export.
    Rich column variety (name, email, phone, status, stage, score, amount,
    timestamps, JSON tags) to exercise Schema Intel classification and
    AskData NL queries meaningfully.
    """
    if os.path.exists(path):
        return
    import sqlite3
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE accounts (
            account_id INTEGER PRIMARY KEY, name TEXT, industry TEXT,
            website TEXT, phone TEXT, address TEXT, city TEXT,
            state TEXT, zip_code TEXT, country TEXT, annual_revenue REAL,
            employee_count INTEGER, created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(account_id),
            first_name TEXT, last_name TEXT, email TEXT, phone TEXT,
            job_title TEXT, department TEXT, is_decision_maker INTEGER,
            lead_source TEXT, created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE opportunities (
            opp_id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(account_id),
            name TEXT, stage TEXT, amount REAL, probability INTEGER,
            close_date DATE, sales_rep TEXT, created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE activities (
            activity_id INTEGER PRIMARY KEY, contact_id INTEGER REFERENCES contacts(contact_id),
            activity_type TEXT, subject TEXT, description TEXT,
            activity_date TIMESTAMP, duration_minutes INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE cases (
            case_id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(account_id),
            subject TEXT, status TEXT, priority TEXT, origin TEXT,
            assigned_to TEXT, created_at TIMESTAMP, resolved_at TIMESTAMP
        )
    """)

    accounts = [
        (1, 'Acme Global', 'Manufacturing', 'https://acmeglobal.com', '+1-555-0100',
         '100 Industrial Blvd', 'Detroit', 'MI', '48201', 'US', 450_000_000, 3200, '2021-03-15 09:00:00'),
        (2, 'NexaTech Solutions', 'Technology', 'https://nexatech.io', '+1-555-0200',
         '200 Innovation Drive', 'San Francisco', 'CA', '94105', 'US', 180_000_000, 850, '2021-06-01 10:30:00'),
        (3, 'Meridian Health', 'Healthcare', 'https://meridianhealth.org', '+1-555-0300',
         '55 Medical Center Dr', 'Boston', 'MA', '02115', 'US', 720_000_000, 5400, '2020-09-12 08:00:00'),
        (4, 'Quantum Retail Group', 'Retail', 'https://quantumretail.io', '+1-555-0400',
         '800 Commerce Parkway', 'Chicago', 'IL', '60601', 'US', 290_000_000, 1800, '2022-01-10 11:15:00'),
        (5, 'Pinnacle Financial', 'Financial Services', 'https://pinnaclefin.com', '+1-555-0500',
         '120 Wall Street', 'New York', 'NY', '10005', 'US', 980_000_000, 4100, '2019-11-20 07:30:00'),
        (6, 'GreenLeaf Agriculture', 'Agriculture', 'https://greenleafag.com', '+1-555-0600',
         '350 Rural Route 2', 'Fresno', 'CA', '93721', 'US', 85_000_000, 420, '2022-05-05 14:00:00'),
        (7, 'Atlas Logistics', 'Transportation', 'https://atlaslogistics.co', '+1-555-0700',
         '99 Warehouse Ave', 'Memphis', 'TN', '38101', 'US', 210_000_000, 2200, '2021-08-18 09:45:00'),
        (8, 'BrightPath Education', 'Education', 'https://brightpath.edu', '+1-555-0800',
         '42 College Road', 'Austin', 'TX', '78701', 'US', 65_000_000, 380, '2023-02-01 10:00:00'),
        (9, 'Vanguard Energy', 'Energy', 'https://vanguardenergy.com', '+1-555-0900',
         '500 Oilfield Lane', 'Houston', 'TX', '77001', 'US', 1_200_000_000, 6800, '2018-07-10 06:00:00'),
        (10, 'Starlight Media Group', 'Media & Entertainment', 'https://starlightmedia.tv', '+1-555-1000',
         '777 Sunset Blvd', 'Los Angeles', 'CA', '90028', 'US', 350_000_000, 1600, '2020-04-22 12:30:00'),
        (11, 'BlueShield Insurance', 'Insurance', 'https://blueshield.com', '+1-555-1100',
         '320 Policy Street', 'Hartford', 'CT', '06101', 'US', 560_000_000, 3100, '2019-05-14 08:15:00'),
        (12, 'Titan Construction', 'Construction', 'https://titanconstruct.com', '+1-555-1200',
         '15 Builder Blvd', 'Denver', 'CO', '80201', 'US', 175_000_000, 1100, '2022-09-01 07:00:00'),
    ]
    c.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", accounts)

    contacts_data = [
        (1, 1, 'Sarah', 'Chen', 'sarah.chen@acmeglobal.com', '+1-555-1001', 'VP Engineering', 'Engineering', 1, 'referral', '2021-04-01'),
        (2, 1, 'Mike', 'O''Brien', 'mike.obrien@acmeglobal.com', '+1-555-1002', 'Procurement Manager', 'Procurement', 0, 'website', '2021-04-15'),
        (3, 1, 'Lisa', 'Patel', 'lisa.patel@acmeglobal.com', '+1-555-1003', 'CTO', 'Engineering', 1, 'event', '2021-05-01'),
        (4, 2, 'James', 'Wong', 'james.wong@nexatech.io', '+1-555-2001', 'CEO', 'Executive', 1, 'referral', '2021-06-15'),
        (5, 2, 'Emily', 'Rodriguez', 'emily.r@nexatech.io', '+1-555-2002', 'Data Engineer', 'Engineering', 0, 'website', '2021-07-01'),
        (6, 2, 'David', 'Kim', 'david.kim@nexatech.io', '+1-555-2003', 'Head of Product', 'Product', 0, 'campaign', '2021-08-20'),
        (7, 3, 'Jennifer', 'Martinez', 'j.martinez@meridianhealth.org', '+1-555-3001', 'Chief Medical Officer', 'Clinical', 1, 'conference', '2020-10-01'),
        (8, 3, 'Robert', 'Thompson', 'r.thompson@meridianhealth.org', '+1-555-3002', 'IT Director', 'Technology', 0, 'referral', '2020-10-15'),
        (9, 4, 'Amanda', 'Williams', 'amanda.w@quantumretail.io', '+1-555-4001', 'VP Operations', 'Operations', 1, 'website', '2022-02-01'),
        (10, 4, 'Kevin', 'Brown', 'kevin.brown@quantumretail.io', '+1-555-4002', 'Supply Chain Lead', 'Logistics', 0, 'referral', '2022-02-15'),
        (11, 4, 'Rachel', 'Garcia', 'rachel.garcia@quantumretail.io', '+1-555-4003', 'CFO', 'Finance', 1, 'event', '2022-03-01'),
        (12, 5, 'Thomas', 'Anderson', 't.anderson@pinnaclefin.com', '+1-555-5001', 'Managing Director', 'Executive', 1, 'referral', '2020-01-05'),
        (13, 5, 'Catherine', 'Lee', 'catherine.lee@pinnaclefin.com', '+1-555-5002', 'Head of Compliance', 'Legal', 1, 'conference', '2020-01-20'),
        (14, 6, 'Maria', 'Gonzalez', 'maria.g@greenleafag.com', '+1-555-6001', 'Owner', 'Executive', 1, 'campaign', '2022-06-01'),
        (15, 7, 'Jason', 'Taylor', 'jason.t@atlaslogistics.co', '+1-555-7001', 'Fleet Manager', 'Operations', 0, 'website', '2021-09-01'),
        (16, 7, 'Stephanie', 'Clark', 'stephanie.c@atlaslogistics.co', '+1-555-7002', 'VP Technology', 'Engineering', 1, 'referral', '2021-09-15'),
        (17, 8, 'Daniel', 'Harris', 'daniel.h@brightpath.edu', '+1-555-8001', 'Dean', 'Academic', 1, 'conference', '2023-02-15'),
        (18, 9, 'Michelle', 'Nguyen', 'michelle.n@vanguardenergy.com', '+1-555-9001', 'Chief Engineer', 'Engineering', 1, 'referral', '2018-08-01'),
        (19, 9, 'Brian', 'White', 'brian.white@vanguardenergy.com', '+1-555-9002', 'Operations Director', 'Operations', 0, 'website', '2018-08-20'),
        (20, 10, 'Nicole', 'Adams', 'nicole.a@starlightmedia.tv', '+1-555-10001', 'Head of Production', 'Production', 1, 'event', '2020-05-15'),
    ]
    c.executemany("INSERT INTO contacts VALUES (?,?,?,?,?,?,?,?,?,?,?)", contacts_data)

    # More interesting opportunity stages reflecting real pipeline
    opps = [
        (1, 1, 'Acme - Data Platform Migration', 'proposal', 850_000, 40, '2026-03-15', 'Sarah Chen', '2025-10-01'),
        (2, 1, 'Acme - IoT Sensor Analytics', 'qualification', 250_000, 15, '2026-06-01', 'Mike O\'Brien', '2025-12-01'),
        (3, 2, 'NexaTech - AI/ML Pipeline', 'closed_won', 420_000, 100, '2025-06-01', 'James Wong', '2025-03-01'),
        (4, 2, 'NexaTech - Data Warehouse Expansion', 'negotiation', 680_000, 75, '2026-02-01', 'David Kim', '2025-08-01'),
        (5, 3, 'Meridian - Patient Analytics Platform', 'proposal', 1_200_000, 50, '2026-04-01', 'Jennifer Martinez', '2025-11-01'),
        (6, 3, 'Meridian - HIPAA Compliance Audit', 'closed_won', 95_000, 100, '2025-08-01', 'Robert Thompson', '2025-07-01'),
        (7, 4, 'Quantum Retail - Inventory Optimization', 'qualification', 350_000, 20, '2026-05-01', 'Amanda Williams', '2026-01-05'),
        (8, 4, 'Quantum Retail - Customer 360', 'proposal', 520_000, 45, '2026-03-01', 'Rachel Garcia', '2025-10-15'),
        (9, 5, 'Pinnacle - Fraud Detection System', 'closed_lost', 950_000, 0, '2025-05-01', 'Thomas Anderson', '2025-01-01'),
        (10, 5, 'Pinnacle - Regulatory Reporting Engine', 'negotiation', 780_000, 80, '2026-03-01', 'Catherine Lee', '2025-09-01'),
        (11, 6, 'GreenLeaf - Precision Agriculture', 'proposal', 180_000, 35, '2026-07-01', 'Maria Gonzalez', '2026-01-15'),
        (12, 7, 'Atlas - Route Optimization AI', 'closed_won', 310_000, 100, '2025-09-15', 'Stephanie Clark', '2025-06-01'),
        (13, 7, 'Atlas - Fleet Telemetry Pipeline', 'qualification', 220_000, 10, '2026-08-01', 'Jason Taylor', '2026-02-01'),
        (14, 9, 'Vanguard - Wellhead Sensor Integration', 'proposal', 1_500_000, 55, '2026-06-01', 'Michelle Nguyen', '2025-12-15'),
        (15, 10, 'Starlight - Content Recommendation Engine', 'qualification', 450_000, 10, '2026-09-01', 'Nicole Adams', '2026-02-10'),
    ]
    c.executemany("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?)", opps)

    activities_data = [
        (1, 1, 'meeting', 'Initial discovery call', 'Discussed data platform migration requirements and timelines.', '2025-10-05 10:00:00', 60),
        (2, 1, 'email', 'Follow-up: architecture overview', 'Sent architecture whitepaper and case studies.', '2025-10-07 14:30:00', 15),
        (3, 1, 'demo', 'Platform demo', 'Live demo of data ingestion, transformation, and visualization capabilities.', '2025-10-20 11:00:00', 90),
        (4, 2, 'call', 'Intro call with procurement', 'Walked through pricing and licensing models.', '2025-12-05 13:00:00', 30),
        (5, 3, 'meeting', 'Technical deep dive', 'Deep dive into architecture with CTO and engineering team.', '2025-11-01 09:30:00', 120),
        (6, 4, 'meeting', 'Executive sponsor meeting', 'Board-level presentation on AI/ML pipeline ROI.', '2025-03-15 15:00:00', 60),
        (7, 4, 'demo', 'ML pipeline product demo', 'Walked through end-to-end ML pipeline builder.', '2025-03-20 10:00:00', 45),
        (8, 5, 'meeting', 'DW expansion scoping', 'Discussed current warehouse bottlenecks and growth projections.', '2025-08-10 14:00:00', 75),
        (9, 7, 'meeting', 'Patient analytics requirements', 'Gathered requirements for population health analytics.', '2025-11-05 11:30:00', 90),
        (10, 7, 'demo', 'HIPAA-compliant analytics demo', 'Demonstrated PHI-safe analytics capabilities.', '2025-11-15 10:00:00', 60),
        (11, 8, 'call', 'IT infrastructure review', 'Reviewed current IT stack for integration planning.', '2020-10-20 09:00:00', 45),
        (12, 9, 'meeting', 'Retail inventory discovery', 'Analyzed current inventory management pain points.', '2026-01-10 13:00:00', 60),
        (13, 11, 'meeting', 'Retail Customer 360 strategy', 'Strategy session for unified customer view.', '2025-10-20 10:00:00', 90),
        (14, 12, 'meeting', 'Fraud detection PITCH', 'Presented ML-based fraud detection approach.', '2025-01-10 14:00:00', 60),
        (15, 13, 'meeting', 'Regulatory reporting deep dive', 'Detailed requirements gathering for SEC/FINRA reporting.', '2025-09-15 10:30:00', 120),
        (16, 14, 'call', 'Agriculture tech landscape', 'Discussed precision farming technology landscape.', '2026-01-20 11:00:00', 45),
        (17, 16, 'meeting', 'Route optimization POC planning', 'Planned proof-of-concept scope and success criteria.', '2025-06-10 14:00:00', 60),
        (18, 16, 'demo', 'Route optimization demo', 'Demonstrated 15% route efficiency improvement in simulation.', '2025-06-25 10:00:00', 90),
        (19, 18, 'meeting', 'Oil well sensor integration', 'Technical scoping for wellhead sensor data integration.', '2025-12-20 09:00:00', 75),
        (20, 20, 'call', 'Content recommendation intro', 'Initial discussion about content personalization needs.', '2026-02-15 14:00:00', 30),
    ]
    c.executemany("INSERT INTO activities VALUES (?,?,?,?,?,?,?)", activities_data)

    cases_data = [
        (1, 1, 'Data pipeline latency exceeding SLA', 'escalated', 'High', 'email', 'Sarah Chen', '2025-11-01 08:00:00', None),
        (2, 1, 'API rate limiting issues', 'open', 'Medium', 'portal', 'Mike O\'Brien', '2025-12-10 09:00:00', None),
        (3, 3, 'PHI data masking not applying', 'resolved', 'Urgent', 'phone', 'Robert Thompson', '2025-09-05 07:30:00', '2025-09-06 14:00:00'),
        (4, 4, 'Inventory sync failure', 'open', 'High', 'email', 'Amanda Williams', '2026-01-20 11:00:00', None),
        (5, 5, 'Reporting dashboard not loading', 'resolved', 'Medium', 'portal', 'Thomas Anderson', '2025-04-10 10:00:00', '2025-04-11 16:30:00'),
        (6, 7, 'GPS data format incompatibility', 'resolved', 'High', 'email', 'Jason Taylor', '2025-08-01 14:00:00', '2025-08-03 10:00:00'),
        (7, 9, 'Sensor data missing timestamps', 'open', 'High', 'phone', 'Michelle Nguyen', '2026-01-15 09:30:00', None),
        (8, 10, 'Transcoding job failed overnight', 'resolved', 'Urgent', 'monitoring_alert', 'Nicole Adams', '2025-06-20 02:00:00', '2025-06-20 08:00:00'),
    ]
    c.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?)", cases_data)
    conn.commit()
    conn.close()
    logger.info("  Seeded CRM source: %s (%d accounts, %d contacts, %d opps, %d activities, %d cases)",
                path, len(accounts), len(contacts_data), len(opps), len(activities_data), len(cases_data))


def seed_dw_target(path: str) -> None:
    """Data Warehouse target — star schema aligned with the CRM source.
    
    4 dimension tables + 2 fact tables at meaningful volume (~500 fact rows)
    so Query Studio pagination, aggregation queries, and Visualize have
    real data to work with.
    """
    if os.path.exists(path):
        return
    import sqlite3
    conn = sqlite3.connect(path)
    c = conn.cursor()

    # Dimensions
    c.execute("""
        CREATE TABLE dim_customer (
            customer_key INTEGER PRIMARY KEY, source_account_id INTEGER,
            customer_name TEXT, industry TEXT, region TEXT, 
            segment TEXT, is_active INTEGER, dw_created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE dim_product (
            product_key INTEGER PRIMARY KEY, product_name TEXT,
            product_category TEXT, unit_price REAL, margin_pct REAL
        )
    """)
    c.execute("""
        CREATE TABLE dim_date (
            date_key INTEGER PRIMARY KEY, full_date DATE,
            year INTEGER, quarter INTEGER, month INTEGER,
            month_name TEXT, week INTEGER, day_of_week INTEGER,
            is_weekend INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE dim_sales_rep (
            rep_key INTEGER PRIMARY KEY, rep_name TEXT,
            territory TEXT, team TEXT, hire_date DATE
        )
    """)

    # Facts
    c.execute("""
        CREATE TABLE fact_revenue (
            revenue_id INTEGER PRIMARY KEY, customer_key INTEGER,
            product_key INTEGER, date_key INTEGER, rep_key INTEGER,
            quantity INTEGER, unit_price REAL, discount_pct REAL,
            net_amount REAL, recognized_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE fact_support (
            support_id INTEGER PRIMARY KEY, customer_key INTEGER,
            date_key INTEGER, severity TEXT, case_hours REAL,
            sla_met INTEGER, resolved_at TIMESTAMP
        )
    """)

    # ── dim_customer ───────────────────────────────────────────
    dim_customers = [
        (1, 1, 'Acme Global', 'Manufacturing', 'North America', 'Enterprise', 1, '2024-01-01'),
        (2, 2, 'NexaTech Solutions', 'Technology', 'North America', 'Enterprise', 1, '2024-01-01'),
        (3, 3, 'Meridian Health', 'Healthcare', 'North America', 'Enterprise', 1, '2024-01-01'),
        (4, 4, 'Quantum Retail Group', 'Retail', 'North America', 'Enterprise', 1, '2024-01-01'),
        (5, 5, 'Pinnacle Financial', 'Financial Services', 'North America', 'Enterprise', 1, '2024-01-01'),
        (6, 6, 'GreenLeaf Agriculture', 'Agriculture', 'North America', 'SMB', 1, '2024-01-01'),
        (7, 7, 'Atlas Logistics', 'Transportation', 'North America', 'Enterprise', 1, '2024-01-01'),
        (8, 8, 'BrightPath Education', 'Education', 'North America', 'SMB', 1, '2024-01-01'),
        (9, 9, 'Vanguard Energy', 'Energy', 'North America', 'Enterprise', 1, '2024-01-01'),
        (10, 10, 'Starlight Media Group', 'Media & Entertainment', 'North America', 'Enterprise', 1, '2024-01-01'),
    ]
    c.executemany("INSERT INTO dim_customer VALUES (?,?,?,?,?,?,?,?)", dim_customers)

    # ── dim_product ─────────────────────────────────────────────
    dim_products = [
        (1, 'Data Ingestion Pipeline', 'Platform', 50_000, 75.0),
        (2, 'ETL Transformation Engine', 'Platform', 35_000, 70.0),
        (3, 'AI/ML Pipeline Builder', 'AI', 80_000, 80.0),
        (4, 'Real-time Analytics Dashboard', 'Visualization', 25_000, 65.0),
        (5, 'Schema Discovery Scanner', 'Catalog', 15_000, 60.0),
        (6, 'Data Quality Monitor', 'Governance', 20_000, 72.0),
        (7, 'API Gateway for Data', 'Integration', 30_000, 68.0),
        (8, 'Compliance Audit Module', 'Security', 40_000, 78.0),
        (9, 'Natural Language Query', 'AI', 60_000, 82.0),
        (10, 'Data Catalog Search', 'Catalog', 12_000, 55.0),
    ]
    c.executemany("INSERT INTO dim_product VALUES (?,?,?,?,?)", dim_products)

    # ── dim_date ────────────────────────────────────────────────
    rng = random.Random(42)
    dim_dates = []
    from datetime import date, timedelta
    d = date(2024, 1, 1)
    end = date(2026, 12, 31)
    while d <= end:
        dim_dates.append((
            int(d.strftime("%Y%m%d")), d.isoformat(), d.year,
            (d.month - 1) // 3 + 1, d.month, d.strftime("%B"),
            d.isocalendar()[1], d.weekday(), 1 if d.weekday() >= 5 else 0,
        ))
        d += timedelta(days=1)
    c.executemany("INSERT INTO dim_date VALUES (?,?,?,?,?,?,?,?,?)", dim_dates)

    # ── dim_sales_rep ───────────────────────────────────────────
    dim_reps = [
        (1, 'Sarah Chen', 'North East', 'Enterprise', '2022-03-01'),
        (2, 'David Kim', 'West Coast', 'Enterprise', '2022-06-15'),
        (3, 'Jennifer Martinez', 'North East', 'Enterprise', '2021-01-10'),
        (4, 'Rachel Garcia', 'Mid West', 'Enterprise', '2023-02-01'),
        (5, 'Thomas Anderson', 'North East', 'Strategic', '2020-05-01'),
        (6, 'Maria Gonzalez', 'West Coast', 'SMB', '2023-06-01'),
        (7, 'Stephanie Clark', 'South', 'Enterprise', '2022-09-01'),
        (8, 'Michelle Nguyen', 'South', 'Strategic', '2019-03-15'),
    ]
    c.executemany("INSERT INTO dim_sales_rep VALUES (?,?,?,?,?)", dim_reps)

    # ── fact_revenue (480 rows) ─────────────────────────────────
    rng = random.Random(20260713)
    revenues = []
    date_keys = [r[0] for r in dim_dates if r[0] >= 20240101 and r[0] <= 20260630]
    for rid in range(1, 481):
        cust = rng.randint(1, 10)
        prod = rng.randint(1, 10)
        dk = rng.choice(date_keys)
        rep = rng.randint(1, 8)
        qty = rng.randint(1, 15)
        price = dim_products[prod - 1][3] * (1 + rng.choice([-0.05, 0, 0.05, 0.1]))
        disc = rng.choice([0, 0, 0, 5, 10, 15]) if rng.random() < 0.3 else 0
        net = round(qty * price * (1 - disc / 100), 2)
        revenues.append((rid, cust, prod, dk, rep, qty, round(price, 2), disc, net, '2025-06-30 12:00:00'))
    c.executemany("INSERT INTO fact_revenue VALUES (?,?,?,?,?,?,?,?,?,?)", revenues)

    # ── fact_support (150 rows) ─────────────────────────────────
    supports = []
    for sid in range(1, 151):
        cust = rng.randint(1, 10)
        dk = rng.choice([r[0] for r in dim_dates if r[0] >= 20240101])
        sev = rng.choice(['Critical', 'High', 'Medium', 'Low'])
        hours = round(rng.uniform(0.5, 40), 1)
        sla = 1 if (sev in ('Low', 'Medium') or hours < 8) else 0
        supports.append((sid, cust, dk, sev, hours, sla, None))
    c.executemany("INSERT INTO fact_support VALUES (?,?,?,?,?,?,?)", supports)

    conn.commit()
    conn.close()
    logger.info("  Seeded DW target: %s (%d dims, %d fact_revenue rows, %d fact_support rows)",
                path, len(dim_customers) + len(dim_products) + len(dim_dates) + len(dim_reps),
                len(revenues), len(supports))


def seed_ecommerce(path: str) -> None:
    """E-Commerce database — products, orders, order_items, customers, reviews.
    
    500 orders across 100 products and 200 customers — substantial enough
    for meaningful aggregation queries (revenue by category, customer LTV,
    monthly trends, etc.).
    """
    if os.path.exists(path):
        return
    import sqlite3
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY, name TEXT, category TEXT,
            subcategory TEXT, price REAL, cost REAL, sku TEXT UNIQUE,
            inventory_count INTEGER, is_active INTEGER, created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT,
            email TEXT UNIQUE, phone TEXT, address_line1 TEXT, city TEXT,
            state TEXT, zip_code TEXT, signup_date DATE,
            loyalty_tier TEXT, total_orders INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY, customer_id INTEGER,
            order_date TIMESTAMP, status TEXT, payment_method TEXT,
            shipping_cost REAL, tax_amount REAL, total_amount REAL,
            shipping_address TEXT
        )
    """)
    c.execute("""
        CREATE TABLE order_items (
            item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id),
            product_id INTEGER REFERENCES products(product_id),
            quantity INTEGER, unit_price REAL, discount_pct REAL
        )
    """)
    c.execute("""
        CREATE TABLE reviews (
            review_id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES products(product_id),
            customer_id INTEGER, rating INTEGER, title TEXT,
            review_text TEXT, created_at TIMESTAMP
        )
    """)

    rng = random.Random(20260713)

    categories = {
        'Electronics': ['Laptops', 'Tablets', 'Smartphones', 'Accessories', 'Audio'],
        'Home & Garden': ['Kitchen', 'Furniture', 'Decor', 'Gardening', 'Tools'],
        'Apparel': ['Men''s Clothing', 'Women''s Clothing', 'Kids', 'Shoes', 'Accessories'],
        'Sports': ['Fitness', 'Outdoor', 'Team Sports', 'Cycling', 'Swimming'],
        'Books': ['Fiction', 'Non-Fiction', 'Technical', 'Children', 'Academic'],
    }
    products_data = []
    for pid in range(1, 101):
        cat = rng.choice(list(categories.keys()))
        sub = rng.choice(categories[cat])
        name = f"{sub} Item #{pid}"
        price = round(rng.uniform(5.99, 599.99), 2)
        cost = round(price * rng.uniform(0.25, 0.65), 2)
        products_data.append((
            pid, name, cat, sub, price, cost,
            f"SKU-{1000 + pid}", rng.randint(0, 500),
            1 if rng.random() > 0.05 else 0,
            f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d} 09:00:00",
        ))
    c.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)", products_data)

    first_names = ['Olivia', 'Ethan', 'Sophia', 'Liam', 'Isabella', 'Noah',
                   'Mia', 'Lucas', 'Charlotte', 'Mason', 'Luna', 'Elijah',
                   'Harper', 'James', 'Evelyn', 'Benjamin', 'Abigail', 'Henry',
                   'Emily', 'Alexander']
    last_names = ['Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller',
                  'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Gonzalez',
                  'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore', 'Jackson']
    tiers = ['Bronze', 'Silver', 'Gold', 'Platinum']
    customers_data = []
    for cid in range(1, 201):
        fn = rng.choice(first_names)
        ln = rng.choice(last_names)
        email = f"{fn.lower()}.{ln.lower()}{rng.randint(1,99)}@example.com"
        customers_data.append((
            cid, fn, ln, email,
            f"+1-555-{rng.randint(1000,9999):04d}",
            f"{rng.randint(1,999)} {rng.choice(['Main','Oak','Elm','Maple','Cedar'])} St",
            rng.choice(['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix',
                        'Philadelphia', 'San Antonio', 'San Diego', 'Dallas', 'Austin']),
            rng.choice(['NY', 'CA', 'IL', 'TX', 'AZ', 'PA', 'FL', 'OH', 'GA', 'WA']),
            f"{rng.randint(10000,99999)}",
            f"2023-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            rng.choices(tiers, weights=[40, 30, 20, 10])[0],
            0,
        ))
    c.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", customers_data)

    statuses = ['delivered', 'delivered', 'delivered', 'shipped', 'processing',
                'cancelled', 'returned', 'delivered', 'delivered', 'shipped']
    payment_methods = ['Credit Card', 'PayPal', 'Bank Transfer', 'Debit Card', 'Apple Pay', 'Google Pay']
    orders_data = []
    for oid in range(1, 501):
        cust = rng.randint(1, 200)
        od = f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d} {rng.randint(8,20):02d}:{rng.randint(0,59):02d}:00"
        shipping = round(rng.uniform(0, 15), 2)
        orders_data.append((
            oid, cust, od, rng.choice(statuses), rng.choice(payment_methods),
            shipping, 0.0, 0.0,  # tax and total filled after items
            f"{rng.randint(1,999)} {rng.choice(['Main','Oak','Elm'])} St",
        ))
    c.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", orders_data)

    # Items — 1 to 5 per order
    items_data = []
    total_by_order = {}
    for oid in range(1, 501):
        num_items = rng.choices([1, 2, 3, 4, 5], weights=[30, 35, 20, 10, 5])[0]
        subtotal = 0.0
        for _ in range(num_items):
            pid = rng.randint(1, 100)
            qty = rng.choices([1, 2, 3, 4], weights=[50, 30, 15, 5])[0]
            price = products_data[pid - 1][4]
            disc = rng.choice([0, 0, 0, 5, 10, 15, 20]) if rng.random() < 0.25 else 0
            items_data.append((len(items_data) + 1, oid, pid, qty, price, disc))
            subtotal += qty * price * (1 - disc / 100)
        total_by_order[oid] = subtotal
    c.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items_data)

    # Update order totals
    for oid, total in total_by_order.items():
        tax = round(total * 0.08, 2)
        shipping = orders_data[oid - 1][6]
        c.execute(
            "UPDATE orders SET tax_amount=?, total_amount=? WHERE order_id=?",
            (tax, round(total + tax + shipping, 2), oid),
        )

    # Reviews — ~300 reviews across products
    reviews_data = []
    for rid in range(1, 301):
        pid = rng.randint(1, 100)
        cust = rng.randint(1, 200)
        rating = rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 20, 35, 30])[0]
        titles = {
            5: ['Amazing!', 'Love it!', 'Perfect', 'Exceeded expectations', 'Highly recommend'],
            4: ['Great product', 'Very good', 'Almost perfect', 'Happy with purchase', 'Solid choice'],
            3: ['Okay', 'Decent', 'Not bad', 'Average', 'Does the job'],
            2: ['Disappointed', 'Not great', 'Below expectations', 'Meh', 'Could be better'],
            1: ['Terrible', 'Waste of money', 'Horrible', 'Avoid', 'Broke in a week'],
        }
        title = rng.choice(titles[rating])
        reviews_data.append((
            rid, pid, cust, rating, title,
            f"Customer review for product #{pid}: {title.lower()}. Ordered via e-commerce platform.",
            f"2024-{rng.randint(1,12):02d}-{rng.randint(1,28):02d} 10:00:00",
        ))
    c.executemany("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)", reviews_data)

    # Update customer order counts
    for cid in range(1, 201):
        cnt = sum(1 for o in orders_data if o[1] == cid)
        c.execute("UPDATE customers SET total_orders=? WHERE customer_id=?", (cnt, cid))

    conn.commit()
    conn.close()
    logger.info("  Seeded E-Commerce: %s (%d products, %d customers, %d orders, %d items, %d reviews)",
                path, len(products_data), len(customers_data), len(orders_data),
                len(items_data), len(reviews_data))


def seed_finance(path: str) -> None:
    """Finance / General Ledger database — accounts, transactions, invoices, budgets.
    
    5 account categories, 1500 transactions across 24 months, budgets per
    department. Realistic financial data for GL queries and aggregation.
    """
    if os.path.exists(path):
        return
    import sqlite3
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE chart_of_accounts (
            account_id INTEGER PRIMARY KEY, account_code TEXT UNIQUE,
            account_name TEXT, account_type TEXT, normal_balance TEXT,
            is_active INTEGER, created_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE transactions (
            txn_id INTEGER PRIMARY KEY, account_id INTEGER,
            txn_date DATE, amount REAL, txn_type TEXT,
            description TEXT, reference TEXT, created_by TEXT
        )
    """)
    c.execute("""
        CREATE TABLE invoices (
            invoice_id INTEGER PRIMARY KEY, invoice_number TEXT UNIQUE,
            customer_name TEXT, invoice_date DATE, due_date DATE,
            line_item_total REAL, tax_amount REAL, total_amount REAL,
            status TEXT, paid_at TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE invoice_line_items (
            line_id INTEGER PRIMARY KEY, invoice_id INTEGER REFERENCES invoices(invoice_id),
            description TEXT, quantity INTEGER, unit_price REAL, total_price REAL
        )
    """)
    c.execute("""
        CREATE TABLE budget_allocations (
            budget_id INTEGER PRIMARY KEY, fiscal_year INTEGER,
            department TEXT, account_code TEXT, allocated_amount REAL,
            spent_to_date REAL, remaining REAL
        )
    """)

    rng = random.Random(20260713)

    # Chart of Accounts
    accounts_data = [
        (1, '1000', 'Cash & Cash Equivalents', 'Asset', 'Debit', 1, '2020-01-01'),
        (2, '1100', 'Accounts Receivable', 'Asset', 'Debit', 1, '2020-01-01'),
        (3, '1200', 'Inventory', 'Asset', 'Debit', 1, '2020-01-01'),
        (4, '1300', 'Prepaid Expenses', 'Asset', 'Debit', 1, '2020-01-01'),
        (5, '1400', 'Property & Equipment', 'Asset', 'Debit', 1, '2020-01-01'),
        (6, '2000', 'Accounts Payable', 'Liability', 'Credit', 1, '2020-01-01'),
        (7, '2100', 'Accrued Expenses', 'Liability', 'Credit', 1, '2020-01-01'),
        (8, '2200', 'Deferred Revenue', 'Liability', 'Credit', 1, '2020-01-01'),
        (9, '2300', 'Long-term Debt', 'Liability', 'Credit', 1, '2020-01-01'),
        (10, '3000', 'Common Stock', 'Equity', 'Credit', 1, '2020-01-01'),
        (11, '3100', 'Retained Earnings', 'Equity', 'Credit', 1, '2020-01-01'),
        (12, '4000', 'Product Revenue', 'Revenue', 'Credit', 1, '2020-01-01'),
        (13, '4100', 'Service Revenue', 'Revenue', 'Credit', 1, '2020-01-01'),
        (14, '4200', 'Interest Income', 'Revenue', 'Credit', 1, '2020-01-01'),
        (15, '5000', 'Cost of Goods Sold', 'Expense', 'Debit', 1, '2020-01-01'),
        (16, '5100', 'Salaries & Wages', 'Expense', 'Debit', 1, '2020-01-01'),
        (17, '5200', 'Rent & Facilities', 'Expense', 'Debit', 1, '2020-01-01'),
        (18, '5300', 'Marketing & Advertising', 'Expense', 'Debit', 1, '2020-01-01'),
        (19, '5400', 'IT & Infrastructure', 'Expense', 'Debit', 1, '2020-01-01'),
        (20, '5500', 'Professional Services', 'Expense', 'Debit', 1, '2020-01-01'),
        (21, '5600', 'Depreciation & Amortization', 'Expense', 'Debit', 1, '2020-01-01'),
        (22, '5700', 'Travel & Entertainment', 'Expense', 'Debit', 1, '2020-01-01'),
        (23, '5800', 'Insurance', 'Expense', 'Debit', 1, '2020-01-01'),
        (24, '5900', 'Taxes & Licenses', 'Expense', 'Debit', 1, '2020-01-01'),
    ]
    c.executemany("INSERT INTO chart_of_accounts VALUES (?,?,?,?,?,?,?)", accounts_data)

    # Transactions — regular operational patterns
    txn_data = []
    for tid in range(1, 1501):
        # Seasonal patterns: expenses higher in Q4, revenue in Q2/Q3
        month = rng.randint(1, 12)
        amount_mult = 1.0
        if month in (10, 11, 12):
            amount_mult = 1.3
        if month in (4, 5, 6, 7):
            amount_mult = 1.15

        acct = rng.randint(1, 24)
        row = accounts_data[acct - 1]
        # Debit accounts have positive amounts for debits, negative for credits
        amount = round(rng.uniform(100, 50000) * amount_mult, 2)
        txn_type = 'debit' if rng.random() < 0.55 else 'credit'
        # Revenue accounts normally have credit balances
        if row[3] == 'Revenue' and txn_type == 'debit':
            txn_type = 'credit'
        if row[3] == 'Expense' and txn_type == 'credit':
            txn_type = 'debit'

        txn_data.append((
            tid, acct,
            f"2025-{month:02d}-{rng.randint(1,28):02d}",
            amount, txn_type,
            rng.choice([
                'Monthly subscription revenue', 'Client payment received',
                'Vendor invoice paid', 'Payroll processing', 'Cloud infrastructure',
                'Office supplies', 'Software license renewal', 'Consulting fees',
                'Advertising spend', 'Insurance premium', 'Utility payment',
                'Equipment lease', 'Training expense', 'Travel reimbursement',
                'Dividend payment', 'Interest earned', 'Refund issued',
                'Account reconciliation adjustment', 'Tax payment', 'Loan payment',
            ]),
            f"REF-{tid:06d}", 'system',
        ))
    c.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?)", txn_data)

    # Invoices — 200 invoices with line items
    inv_data = []
    line_data = []
    for iid in range(1, 201):
        inv_num = f"INV-2025-{iid:04d}"
        cust = rng.choice([
            'Acme Global', 'NexaTech Solutions', 'Meridian Health',
            'Quantum Retail', 'Pinnacle Financial', 'Atlas Logistics',
        ])
        inv_date = f"2025-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
        # 1-5 line items
        num_lines = rng.randint(1, 5)
        subtotal = 0.0
        for li in range(num_lines):
            desc = rng.choice([
                'Data Pipeline License', 'Consulting Services (hourly)',
                'Cloud Infrastructure Setup', 'Data Migration Service',
                'Training & Onboarding', 'Premium Support Retainer',
                'Custom Dashboard Development', 'API Integration',
            ])
            qty = rng.choices([1, 2, 3, 5, 10, 20, 40], weights=[20, 15, 10, 15, 20, 10, 10])[0]
            unit_price = round(rng.uniform(50, 5000), 2)
            total = round(qty * unit_price, 2)
            subtotal += total
            line_data.append((len(line_data) + 1, iid, desc, qty, unit_price, total))
        tax = round(subtotal * 0.08, 2)
        inv_data.append((
            iid, inv_num, cust, inv_date,
            f"2025-{rng.randint(int(inv_date[5:7]), 12):02d}-{rng.randint(1,28):02d}",
            subtotal, tax, round(subtotal + tax, 2),
            rng.choice(['paid', 'paid', 'paid', 'pending', 'overdue', 'cancelled']),
            None,
        ))
    c.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?)", inv_data)
    c.executemany("INSERT INTO invoice_line_items VALUES (?,?,?,?,?,?)", line_data)

    # Budget allocations
    depts = ['Engineering', 'Sales', 'Marketing', 'Finance', 'HR', 'Operations']
    budget_data = []
    for dept in depts:
        for year in (2025, 2026):
            for acct_code in ['5100', '5200', '5300', '5400', '5500', '5700', '5800']:
                alloc = round(rng.uniform(50000, 500000), 2)
                spent = round(alloc * rng.uniform(0.1, 0.9), 2) if year == 2025 else 0.0
                budget_data.append((
                    len(budget_data) + 1, year, dept, acct_code, alloc, spent,
                    round(alloc - spent, 2),
                ))
    c.executemany("INSERT INTO budget_allocations VALUES (?,?,?,?,?,?,?)", budget_data)

    conn.commit()
    conn.close()
    logger.info("  Seeded Finance GL: %s (%d accounts, %d transactions, %d invoices, %d line items, %d budgets)",
                path, len(accounts_data), len(txn_data), len(inv_data), len(line_data), len(budget_data))


def seed_hr(path: str) -> None:
    """HR / Payroll database — employees, departments, payroll, attendance, performance.
    
    ~80 employees across 8 departments with 24 months of payroll history
    and performance review records. The `ssn` column gives Schema Intel
    PII classification genuine signal.
    """
    import sqlite3
    if os.path.exists(path):
        try:
            existing = sqlite3.connect(path)
            employee_count = existing.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
            existing.close()
            if employee_count == 80:
                return
        except (sqlite3.Error, TypeError):
            pass
        # This file is a generated demo artifact. A prior interrupted seed
        # must be rebuilt instead of being mistaken for a complete dataset.
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE employees (
            employee_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT,
            email TEXT UNIQUE, phone TEXT, ssn TEXT, date_of_birth DATE,
            hire_date DATE, department_id INTEGER, job_title TEXT,
            salary REAL, bonus_target_pct REAL, manager_id INTEGER,
            employment_status TEXT, is_active INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE departments (
            department_id INTEGER PRIMARY KEY, department_name TEXT,
            cost_center TEXT, location TEXT, budget REAL, head_count_target INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE payroll (
            payroll_id INTEGER PRIMARY KEY, employee_id INTEGER,
            pay_period TEXT, gross_pay REAL, tax_deduction REAL,
            retirement_contrib REAL, health_insurance REAL,
            net_pay REAL, pay_date DATE, pay_type TEXT
        )
    """)
    c.execute("""
        CREATE TABLE attendance (
            attendance_id INTEGER PRIMARY KEY, employee_id INTEGER,
            work_date DATE, status TEXT, hours_worked REAL,
            overtime_hours REAL
        )
    """)
    c.execute("""
        CREATE TABLE performance_reviews (
            review_id INTEGER PRIMARY KEY, employee_id INTEGER,
            review_date DATE, reviewer_id INTEGER, overall_rating REAL,
            category_scores TEXT, comments TEXT
        )
    """)

    rng = random.Random(20260713)

    # Departments
    departments_data = [
        (1, 'Engineering', 'CC-ENG-01', 'San Francisco, CA', 5_000_000, 25),
        (2, 'Sales', 'CC-SLS-01', 'New York, NY', 3_500_000, 20),
        (3, 'Marketing', 'CC-MKT-01', 'Austin, TX', 2_000_000, 12),
        (4, 'Human Resources', 'CC-HR-01', 'Chicago, IL', 1_200_000, 8),
        (5, 'Finance', 'CC-FIN-01', 'Boston, MA', 2_500_000, 10),
        (6, 'Operations', 'CC-OPS-01', 'Denver, CO', 3_000_000, 15),
        (7, 'Legal', 'CC-LGL-01', 'Washington, DC', 1_800_000, 6),
        (8, 'Product', 'CC-PROD-01', 'San Francisco, CA', 2_800_000, 14),
    ]
    c.executemany("INSERT INTO departments VALUES (?,?,?,?,?,?)", departments_data)

    first_names = ['James', 'Mary', 'Robert', 'Patricia', 'John', 'Jennifer',
                   'Michael', 'Linda', 'David', 'Elizabeth', 'William', 'Barbara',
                   'Richard', 'Susan', 'Joseph', 'Jessica', 'Thomas', 'Sarah',
                   'Charles', 'Karen', 'Christopher', 'Nancy', 'Daniel', 'Lisa',
                   'Matthew', 'Margaret', 'Anthony', 'Betty', 'Mark', 'Sandra']
    last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia',
                  'Miller', 'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez',
                  'Gonzalez', 'Wilson', 'Anderson', 'Thomas', 'Taylor', 'Moore',
                  'Jackson', 'Martin', 'Lee', 'Perez', 'Thompson', 'White',
                  'Harris', 'Sanchez', 'Clark', 'Ramirez', 'Lewis', 'Robinson']
    job_titles_by_dept = {
        1: ['Software Engineer', 'Senior Engineer', 'Staff Engineer', 'Engineering Manager',
            'DevOps Engineer', 'Data Engineer', 'QA Engineer', 'Principal Architect'],
        2: ['Sales Representative', 'Account Executive', 'Sales Manager', 'VP of Sales',
            'Sales Operations Analyst', 'Enterprise Account Executive', 'Sales Engineer'],
        3: ['Marketing Coordinator', 'Marketing Manager', 'Content Strategist', 'SEO Specialist',
            'Brand Manager', 'Demand Generation Manager', 'Marketing Director'],
        4: ['HR Coordinator', 'HR Generalist', 'HR Manager', 'Recruiter', 'Benefits Specialist',
            'HR Director', 'Payroll Specialist'],
        5: ['Staff Accountant', 'Senior Accountant', 'Financial Analyst', 'Controller',
            'Finance Manager', 'Tax Specialist', 'CFO'],
        6: ['Operations Analyst', 'Operations Manager', 'Supply Chain Coordinator',
            'Logistics Manager', 'Facilities Manager', 'Operations Director'],
        7: ['Corporate Counsel', 'Legal Assistant', 'Compliance Officer', 'General Counsel',
            'Paralegal', 'Contracts Manager'],
        8: ['Product Manager', 'Senior Product Manager', 'Product Designer', 'Product Analyst',
            'Director of Product', 'UX Researcher', 'Product Operations Manager'],
    }

    employees_data = []
    manager_pool = []
    for eid in range(1, 81):
        fn = rng.choice(first_names)
        ln = rng.choice(last_names)
        email = f"{fn.lower()}.{ln.lower()}.{eid}@dataplane.ai"
        dept = rng.randint(1, 8)
        titles = job_titles_by_dept[dept]
        title = rng.choice(titles)
        base_salary = 0
        if 'Director' in title or 'VP' in title or 'CFO' in title or 'General Counsel' in title:
            base_salary = rng.randint(180_000, 300_000)
        elif 'Manager' in title or 'Lead' in title or 'Principal' in title or 'Controller' in title:
            base_salary = rng.randint(130_000, 180_000)
        elif 'Senior' in title or 'Staff' in title:
            base_salary = rng.randint(100_000, 140_000)
        else:
            base_salary = rng.randint(65_000, 100_000)
        base_salary = round(base_salary, -3)  # Round to nearest 1000

        # SSN — area 900-999 (safe synthetic range)
        ssn = f"{rng.randint(900,999)}-{rng.randint(10,99):02d}-{rng.randint(1000,9999):04d}"

        manager = rng.choice(manager_pool) if manager_pool and eid > 5 else None

        employees_data.append((
            eid, fn, ln, email,
            f"+1-555-{rng.randint(1000,9999):04d}",
            ssn,
            f"{rng.randint(1965,2000)}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            f"2020-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}",
            dept, title, base_salary,
            rng.choice([5, 10, 10, 15, 20]),
            manager or None,
            rng.choice(['Active', 'Active', 'Active', 'Active', 'On Leave', 'Terminated']),
            1 if rng.random() > 0.08 else 0,
        ))

        # First few employees are managers
        if eid <= 12:
            manager_pool.append(eid)

    c.executemany("INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", employees_data)

    # Payroll — 24 months for each active employee
    payroll_data = []
    for eid in range(1, 81):
        emp = employees_data[eid - 1]
        if emp[14] == 0:  # not active
            continue
        salary = emp[10]
        monthly_gross = round(salary / 12, 2)
        for year in (2024, 2025):
            for month in range(1, 13):
                gross = monthly_gross
                # Bonus month (March)
                bonus = round(gross * (emp[11] / 100)) if month == 3 else 0
                gross_with_bonus = gross + bonus
                tax = round(gross_with_bonus * 0.28, 2)
                ret = round(gross_with_bonus * 0.06, 2)
                health = 650.00
                net = round(gross_with_bonus - tax - ret - health, 2)
                payroll_data.append((
                    len(payroll_data) + 1, eid,
                    f"{year}-{month:02d}",
                    gross_with_bonus, tax, ret, health, net,
                    f"{year}-{month:02d}-28",
                    'Regular',
                ))
    c.executemany("INSERT INTO payroll VALUES (?,?,?,?,?,?,?,?,?,?)", payroll_data)

    # Attendance — 1 year of daily attendance for active employees
    attendance_data = []
    from datetime import date, timedelta
    start = date(2025, 1, 1)
    end = date(2025, 12, 31)
    for eid in range(1, 81):
        emp = employees_data[eid - 1]
        if emp[14] == 0:
            continue
        d = start
        while d <= end:
            if d.weekday() >= 5:  # weekend
                d += timedelta(days=1)
                continue
            status = rng.choices(
                ['Present', 'Present', 'Present', 'Late', 'Absent'],
                weights=[80, 10, 5, 3, 2],
            )[0]
            hours = 8.0 if status == 'Present' else (7.0 if status == 'Late' else 0)
            overtime = round(rng.uniform(0, 2), 1) if (rng.random() < 0.1 and status == 'Present') else 0.0
            attendance_data.append((
                len(attendance_data) + 1, eid, d.isoformat(),
                status, hours, overtime,
            ))
            d += timedelta(days=1)
    c.executemany("INSERT INTO attendance VALUES (?,?,?,?,?,?)", attendance_data)

    # Performance reviews — ~2 per employee
    review_data = []
    for eid in range(1, 81):
        emp = employees_data[eid - 1]
        if emp[14] == 0:
            continue
        for yr in (2023, 2024):
            rating = round(rng.uniform(2.0, 5.0), 1)
            categories = {
                'technical_skills': round(rng.uniform(2, 5), 1),
                'communication': round(rng.uniform(2, 5), 1),
                'teamwork': round(rng.uniform(2, 5), 1),
                'delivery': round(rng.uniform(2, 5), 1),
                'leadership': round(rng.uniform(2, 5), 1),
            }
            comments = rng.choice([
                "Strong performer exceeding expectations.",
                "Consistent contributor with room for growth.",
                "Excellent team player with technical depth.",
                "Meets expectations reliably.",
                "Outstanding performance this year.",
            ])
            review_data.append((
                len(review_data) + 1, eid,
                f"{yr}-{rng.randint(11,12):02d}-{rng.randint(1,15):02d}",
                emp[12], rating, str(categories), comments,
            ))
    c.executemany("INSERT INTO performance_reviews VALUES (?,?,?,?,?,?,?)", review_data)

    conn.commit()
    conn.close()
    logger.info("  Seeded HR: %s (%d employees, %d payroll records, %d attendance days, %d reviews)",
                path, len(employees_data), len(payroll_data), len(attendance_data), len(review_data))


# ── Load / unload / status ─────────────────────────────────────────────────────

def is_demo_data_loaded(db: Session, *, owner_email: str) -> bool:
    """True if the caller already has any (non-deleted) demo DBConnection
    row of their own (tenant_isolation_tasks slice #1 — each user gets
    their own copy, not a shared global one)."""
    return (
        db.query(DBConnection)
        .filter(
            DBConnection.name.in_(DEMO_CONNECTION_NAMES),
            DBConnection.owner_email == owner_email,
            DBConnection.is_deleted == False,  # noqa: E712
        )
        .first()
        is not None
    )


def load_demo_data(db: Session, *, actor: str, owner_email: str) -> dict:
    """Seed the physical sample databases (shared, idempotent files on
    disk) and register the caller's own DBConnection rows pointing at
    them. Idempotent per-owner — safe to call when some or all of the
    caller's demo rows already exist."""
    os.makedirs(DEMO_DATA_DIR, exist_ok=True)
    try:
        os.chmod(DEMO_DATA_DIR, 0o777)
    except OSError:
        pass

    seed_crm_source(f"{DEMO_DATA_DIR}/dataplane_crm_source.db")
    seed_dw_target(f"{DEMO_DATA_DIR}/dataplane_dw_target.db")
    seed_ecommerce(f"{DEMO_DATA_DIR}/dataplane_ecommerce.db")
    seed_finance(f"{DEMO_DATA_DIR}/dataplane_oracle_sim_FINDB.db")
    seed_hr(f"{DEMO_DATA_DIR}/dataplane_hr_postgres.db")

    # E2E Retail dataset (already rich — 1500 rows with PII/drift patterns)
    from app.core.e2e_seed_data import (
        seed_e2e_retail_analytics,
        seed_e2e_retail_warehouse,
    )
    e2e_retail_path = seed_e2e_retail_analytics(DEMO_DATA_DIR)
    e2e_warehouse_path = seed_e2e_retail_warehouse(DEMO_DATA_DIR)

    def _owned(name: str):
        """Only an already-active row counts as \"already loaded\" — a
        previously unloaded (soft-deleted) row of the same name must not
        block re-loading, since the partial unique index only enforces
        uniqueness among non-deleted rows."""
        return (
            db.query(DBConnection)
            .filter(DBConnection.name == name, DBConnection.owner_email == owner_email,
                    DBConnection.is_deleted == False)  # noqa: E712
            .first()
        )

    created = 0
    if not _owned("CRM_Source_Analytics"):
        db.add(DBConnection(name="CRM_Source_Analytics", type="sqlite",
                            config={"path": f"{DEMO_DATA_DIR}/dataplane_crm_source.db"},
                            owner_email=owner_email, created_by=actor, updated_by=actor))
        db.add(DBConnection(name="Data_Warehouse_Target", type="sqlite",
                            config={"path": f"{DEMO_DATA_DIR}/dataplane_dw_target.db"},
                            owner_email=owner_email, created_by=actor, updated_by=actor))
        db.add(DBConnection(name="ECommerce_MySQL", type="sqlite",
                            config={"path": f"{DEMO_DATA_DIR}/dataplane_ecommerce.db"},
                            owner_email=owner_email, created_by=actor, updated_by=actor))
        db.add(DBConnection(
            name="Finance_Oracle",
            type="oracle",
            config={"host": "localhost-sim", "port": 1521, "service_name": "FINDB",
                    "user": "finance_user", "password": "****"},
            owner_email=owner_email, created_by=actor, updated_by=actor,
        ))
        db.add(DBConnection(
            name="HR_Postgres",
            type="postgres",
            config={"host": "postgres", "port": 5432, "dbname": "dataplane",
                    "user": "postgres", "password": "postgres"},
            owner_email=owner_email, created_by=actor, updated_by=actor,
        ))
        db.add(DBConnection(
            name="E2E_Retail_Analytics", type="sqlite",
            config={"path": e2e_retail_path},
            owner_email=owner_email, created_by=actor, updated_by=actor,
        ))
        db.commit()
        created += 6
        logger.info("Seeded 6 DBConnection rows for owner=%s", owner_email)
    if not _owned("E2E_Retail_Warehouse"):
        db.add(DBConnection(
            name="E2E_Retail_Warehouse", type="sqlite",
            config={"path": e2e_warehouse_path},
            owner_email=owner_email, created_by=actor, updated_by=actor,
        ))
        db.commit()
        created += 1
        logger.info("Seeded E2E_Retail_Warehouse connection for owner=%s", owner_email)

    record_audit(db, "demo_data_loaded", actor=actor,
                 payload={"connections_created": created, "owner_email": owner_email})
    return {"loaded": True, "connections_created": created}


def unload_demo_data(db: Session, *, actor: str, owner_email: str) -> dict:
    """Soft-delete the caller's own demo DBConnection rows (mirrors
    ConnectionService.soft_delete_connection's convention). Physical sample
    database files are left on disk — inert, and lets a later reload skip
    re-seeding them."""
    rows = (
        db.query(DBConnection)
        .filter(
            DBConnection.name.in_(DEMO_CONNECTION_NAMES),
            DBConnection.owner_email == owner_email,
            DBConnection.is_deleted == False,  # noqa: E712
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for row in rows:
        row.is_deleted = True
        row.deleted_at = now
        row.updated_by = actor
    db.commit()

    record_audit(db, "demo_data_removed", actor=actor,
                 payload={"connections_removed": len(rows), "owner_email": owner_email})
    logger.info("Removed %d demo DBConnection row(s) (owner=%s, actor=%s)",
                len(rows), owner_email, actor)
    return {"loaded": False, "connections_removed": len(rows)}
