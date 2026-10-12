-- dataPlane Postgres Seed: HR Domain
-- Executed on startup by Docker postgres init

CREATE TABLE IF NOT EXISTS employees (
    employee_id   SERIAL PRIMARY KEY,
    first_name    VARCHAR(50) NOT NULL,
    last_name     VARCHAR(50) NOT NULL,
    email         VARCHAR(100) UNIQUE NOT NULL,
    phone         VARCHAR(20),
    hire_date     DATE NOT NULL,
    department_id INTEGER,
    job_title     VARCHAR(80),
    salary        DECIMAL(12,2),
    ssn           VARCHAR(11)
);

CREATE TABLE IF NOT EXISTS departments (
    department_id   SERIAL PRIMARY KEY,
    department_name VARCHAR(60) NOT NULL,
    manager_id      INTEGER,
    location        VARCHAR(100),
    budget          DECIMAL(15,2)
);

CREATE TABLE IF NOT EXISTS payroll (
    payroll_id    SERIAL PRIMARY KEY,
    employee_id   INTEGER REFERENCES employees(employee_id),
    pay_period    VARCHAR(10),
    gross_pay     DECIMAL(12,2),
    tax_deduction DECIMAL(12,2),
    net_pay       DECIMAL(12,2),
    pay_date      DATE,
    bank_account  VARCHAR(30)
);

-- Seed departments
INSERT INTO departments (department_name, manager_id, location, budget) VALUES
    ('Engineering',   1, 'San Francisco, CA', 2500000.00),
    ('Sales',         2, 'New York, NY',      1800000.00),
    ('Human Resources', 3, 'Chicago, IL',     950000.00),
    ('Finance',       4, 'Boston, MA',        1200000.00),
    ('Marketing',     5, 'Austin, TX',        1100000.00),
    ('Data & Analytics', 6, 'Seattle, WA',    1600000.00),
    ('Customer Support', 7, 'Phoenix, AZ',    850000.00),
    ('Legal',         8, 'Washington, DC',    700000.00),
    ('Product Management', 9, 'San Francisco, CA', 1400000.00),
    ('IT Operations', 10, 'Denver, CO',       980000.00)
ON CONFLICT DO NOTHING;

-- Seed employees
INSERT INTO employees (first_name, last_name, email, phone, hire_date, department_id, job_title, salary, ssn) VALUES
    ('James',    'Anderson', 'james.anderson@company.com',  '+1-555-3001', '2022-03-15', 1, 'Sr. Engineer',       145000.00, '***-**-1234'),
    ('Maria',    'Garcia',   'maria.garcia@company.com',    '+1-555-3002', '2021-07-20', 2, 'Sales Director',     165000.00, '***-**-2345'),
    ('Chen',     'Wei',      'chen.wei@company.com',        '+1-555-3003', '2023-01-10', 3, 'HR Manager',         120000.00, '***-**-3456'),
    ('Sarah',    'Johnson',  'sarah.johnson@company.com',   '+1-555-3004', '2020-11-01', 4, 'Finance Director',   155000.00, '***-**-4567'),
    ('Ahmed',    'Hassan',   'ahmed.hassan@company.com',    '+1-555-3005', '2023-06-15', 5, 'Marketing Manager',  125000.00, '***-**-5678'),
    ('Lisa',     'Park',     'lisa.park@company.com',       '+1-555-3006', '2022-09-01', 1, 'DevOps Engineer',    135000.00, '***-**-6789'),
    ('Michael',  'Brown',    'michael.brown@company.com',   '+1-555-3007', '2021-04-12', 2, 'Account Executive',  110000.00, '***-**-7890'),
    ('Priya',    'Sharma',   'priya.sharma@company.com',    '+1-555-3008', '2024-01-08', 1, 'Software Engineer',  125000.00, '***-**-8901'),
    ('Robert',   'Taylor',   'robert.taylor@company.com',   '+1-555-3009', '2023-09-01', 6, 'Data Engineer',      140000.00, '***-**-9012'),
    ('Amanda',   'Clark',    'amanda.clark@company.com',    '+1-555-3010', '2022-06-15', 6, 'Data Scientist',     150000.00, '***-**-0123'),
    ('David',    'Wright',   'david.wright@company.com',    '+1-555-3011', '2021-11-01', 7, 'Support Manager',    95000.00,  '***-**-1123'),
    ('Sophia',   'Lopez',    'sophia.lopez@company.com',    '+1-555-3012', '2024-03-01', 8, 'Legal Counsel',      130000.00, '***-**-2123'),
    ('Daniel',   'Martinez', 'daniel.martinez@company.com', '+1-555-3013', '2023-04-10', 9, 'Product Manager',    135000.00, '***-**-3123'),
    ('Emma',     'Wilson',   'emma.wilson@company.com',     '+1-555-3014', '2022-08-20', 10, 'IT Operations Lead', 115000.00, '***-**-4123'),
    ('Ryan',     'Kim',      'ryan.kim@company.com',        '+1-555-3015', '2024-06-01', 1, 'ML Engineer',        155000.00, '***-**-5123'),
    ('Olivia',   'Davis',    'olivia.davis@company.com',    '+1-555-3016', '2023-02-15', 5, 'Content Strategist', 85000.00,  '***-**-6123'),
    ('Ethan',    'Miller',   'ethan.miller@company.com',    '+1-555-3017', '2021-09-01', 2, 'Sales Rep',          95000.00,  '***-**-7123'),
    ('Ava',      'Garcia',   'ava.garcia@company.com',      '+1-555-3018', '2024-04-15', 3, 'Recruiter',          75000.00,  '***-**-8123'),
    ('Mason',    'Lee',      'mason.lee@company.com',       '+1-555-3019', '2022-12-01', 4, 'Accountant',         85000.00,  '***-**-9123'),
    ('Isabella', 'White',    'isabella.white@company.com',  '+1-555-3020', '2023-07-10', 6, 'BI Analyst',         110000.00, '***-**-0132')
ON CONFLICT DO NOTHING;

-- Seed payroll
INSERT INTO payroll (employee_id, pay_period, gross_pay, tax_deduction, net_pay, pay_date, bank_account) VALUES
    (1, '2025-06', 12083.33, 3625.00,  8458.33, '2025-06-30', 'XXXX-1234'),
    (2, '2025-06', 13750.00, 4125.00,  9625.00, '2025-06-30', 'XXXX-2345'),
    (3, '2025-06', 10000.00, 3000.00,  7000.00, '2025-06-30', 'XXXX-3456'),
    (4, '2025-06', 12916.67, 3875.00,  9041.67, '2025-06-30', 'XXXX-4567'),
    (5, '2025-06', 10416.67, 3125.00,  7291.67, '2025-06-30', 'XXXX-5678'),
    (6, '2025-06', 11250.00, 3375.00,  7875.00, '2025-06-30', 'XXXX-6789'),
    (7, '2025-06',  9166.67, 2750.00,  6416.67, '2025-06-30', 'XXXX-7890'),
    (8, '2025-06', 10416.67, 3125.00,  7291.67, '2025-06-30', 'XXXX-8901'),
    (9, '2025-06', 11666.67, 3500.00,  8166.67, '2025-06-30', 'XXXX-9012'),
    (10, '2025-06', 12500.00, 3750.00,  8750.00, '2025-06-30', 'XXXX-0123'),
    (11, '2025-06',  7916.67, 2375.00,  5541.67, '2025-06-30', 'XXXX-1123'),
    (12, '2025-06', 10833.33, 3250.00,  7583.33, '2025-06-30', 'XXXX-2123'),
    (13, '2025-06', 11250.00, 3375.00,  7875.00, '2025-06-30', 'XXXX-3123'),
    (14, '2025-06',  9583.33, 2875.00,  6708.33, '2025-06-30', 'XXXX-4123'),
    (15, '2025-06', 12916.67, 3875.00,  9041.67, '2025-06-30', 'XXXX-5123'),
    (16, '2025-06',  7083.33, 2125.00,  4958.33, '2025-06-30', 'XXXX-6123'),
    (17, '2025-06',  7916.67, 2375.00,  5541.67, '2025-06-30', 'XXXX-7123'),
    (18, '2025-06',  6250.00, 1875.00,  4375.00, '2025-06-30', 'XXXX-8123'),
    (19, '2025-06',  7083.33, 2125.00,  4958.33, '2025-06-30', 'XXXX-9123'),
    (20, '2025-06',  9166.67, 2750.00,  6416.67, '2025-06-30', 'XXXX-0132'),
    (1, '2025-07', 12083.33, 3625.00,  8458.33, '2025-07-31', 'XXXX-1234'),
    (2, '2025-07', 13750.00, 4125.00,  9625.00, '2025-07-31', 'XXXX-2345'),
    (3, '2025-07', 10000.00, 3000.00,  7000.00, '2025-07-31', 'XXXX-3456'),
    (4, '2025-07', 12916.67, 3875.00,  9041.67, '2025-07-31', 'XXXX-4567'),
    (5, '2025-07', 10416.67, 3125.00,  7291.67, '2025-07-31', 'XXXX-5678'),
    (6, '2025-07', 11250.00, 3375.00,  7875.00, '2025-07-31', 'XXXX-6789'),
    (7, '2025-07',  9166.67, 2750.00,  6416.67, '2025-07-31', 'XXXX-7890'),
    (8, '2025-07', 10416.67, 3125.00,  7291.67, '2025-07-31', 'XXXX-8901'),
    (9, '2025-07', 11666.67, 3500.00,  8166.67, '2025-07-31', 'XXXX-9012'),
    (10, '2025-07', 12500.00, 3750.00,  8750.00, '2025-07-31', 'XXXX-0123'),
    (11, '2025-07',  7916.67, 2375.00,  5541.67, '2025-07-31', 'XXXX-1123'),
    (12, '2025-07', 10833.33, 3250.00,  7583.33, '2025-07-31', 'XXXX-2123'),
    (13, '2025-07', 11250.00, 3375.00,  7875.00, '2025-07-31', 'XXXX-3123'),
    (14, '2025-07',  9583.33, 2875.00,  6708.33, '2025-07-31', 'XXXX-4123'),
    (15, '2025-07', 12916.67, 3875.00,  9041.67, '2025-07-31', 'XXXX-5123'),
    (16, '2025-07',  7083.33, 2125.00,  4958.33, '2025-07-31', 'XXXX-6123'),
    (17, '2025-07',  7916.67, 2375.00,  5541.67, '2025-07-31', 'XXXX-7123'),
    (18, '2025-07',  6250.00, 1875.00,  4375.00, '2025-07-31', 'XXXX-8123'),
    (19, '2025-07',  7083.33, 2125.00,  4958.33, '2025-07-31', 'XXXX-9123'),
    (20, '2025-07',  9166.67, 2750.00,  6416.67, '2025-07-31', 'XXXX-0132')
ON CONFLICT DO NOTHING;