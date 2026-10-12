-- dataPlane MySQL Seed: E-Commerce Domain
-- Used when MySQL container is present

CREATE DATABASE IF NOT EXISTS ecommerce;
USE ecommerce;

CREATE TABLE IF NOT EXISTS products (
    product_id   INT AUTO_INCREMENT PRIMARY KEY,
    name         VARCHAR(100) NOT NULL,
    category     VARCHAR(50),
    price        DECIMAL(10,2),
    stock_qty    INT DEFAULT 0,
    sku          VARCHAR(20) UNIQUE
);

CREATE TABLE IF NOT EXISTS orders (
    order_id         INT AUTO_INCREMENT PRIMARY KEY,
    customer_email   VARCHAR(100),
    product_id       INT,
    quantity         INT,
    total_amount     DECIMAL(10,2),
    order_date       DATETIME,
    shipping_address TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id      INT AUTO_INCREMENT PRIMARY KEY,
    full_name        VARCHAR(100),
    email            VARCHAR(100) UNIQUE,
    phone            VARCHAR(20),
    address          TEXT,
    city             VARCHAR(50),
    state            VARCHAR(20),
    zip_code         VARCHAR(10),
    credit_card_last4 VARCHAR(4)
);

INSERT INTO products (name, category, price, stock_qty, sku) VALUES
    ('Wireless Mouse', 'Electronics', 29.99, 150, 'WM-001'),
    ('USB-C Hub', 'Electronics', 49.99, 80, 'UC-002'),
    ('Standing Desk', 'Furniture', 399.99, 25, 'SD-003'),
    ('Monitor Arm', 'Accessories', 89.99, 60, 'MA-004'),
    ('Mechanical Keyboard', 'Electronics', 129.99, 45, 'MK-005'),
    ('Noise Cancelling Headphones', 'Electronics', 249.99, 35, 'NC-006'),
    ('Ergonomic Office Chair', 'Furniture', 599.99, 15, 'EC-007'),
    ('4K Webcam', 'Electronics', 179.99, 40, 'WC-008'),
    ('Laptop Stand', 'Accessories', 39.99, 100, 'LS-009'),
    ('Wireless Charging Pad', 'Electronics', 24.99, 200, 'WP-010'),
    ('Desk LED Lamp', 'Accessories', 49.99, 75, 'DL-011'),
    ('Cable Management Kit', 'Accessories', 19.99, 120, 'CM-012'),
    ('Blue Light Blocking Glasses', 'Accessories', 34.99, 90, 'BG-013'),
    ('Portable SSD 1TB', 'Electronics', 129.99, 55, 'SS-014'),
    ('USB Microphone', 'Electronics', 89.99, 65, 'UM-015'),
    ('Smart Power Strip', 'Electronics', 44.99, 85, 'SP-016'),
    ('Office Desk Mat', 'Accessories', 29.99, 110, 'DM-017'),
    ('External DVD Drive', 'Electronics', 34.99, 30, 'ED-018'),
    ('Vertical Mouse', 'Electronics', 39.99, 70, 'VM-019'),
    ('Conference Speaker', 'Electronics', 199.99, 20, 'CS-020');

INSERT INTO customers (full_name, email, phone, address, city, state, zip_code, credit_card_last4) VALUES
    ('Alice Thompson', 'alice@shop.com', '+1-555-2001', '123 Main St', 'New York', 'NY', '10001', '4242'),
    ('Bob Martinez', 'bob@shop.com', '+1-555-2002', '456 Oak Ave', 'Los Angeles', 'CA', '90001', '1234'),
    ('Carol Chen', 'carol@shop.com', '+1-555-2003', '789 Pine Rd', 'Houston', 'TX', '77001', '5678'),
    ('David Kim', 'david.kim@shop.com', '+1-555-2004', '321 Elm Blvd', 'Seattle', 'WA', '98101', '9012'),
    ('Elena Rodriguez', 'elena.r@shop.com', '+1-555-2005', '654 Maple Dr', 'Miami', 'FL', '33101', '3456'),
    ('Frank Okafor', 'frank.o@shop.com', '+1-555-2006', '987 Cedar Ln', 'Atlanta', 'GA', '30301', '7890'),
    ('Grace Patel', 'grace.p@shop.com', '+1-555-2007', '147 Birch Ct', 'Chicago', 'IL', '60601', '2345'),
    ('Henry Nguyen', 'henry.n@shop.com', '+1-555-2008', '258 Walnut Way', 'Portland', 'OR', '97201', '6789'),
    ('Isabella Santos', 'isabella.s@shop.com', '+1-555-2009', '369 Spruce Ave', 'Denver', 'CO', '80201', '1112'),
    ('James Wilson', 'james.w@shop.com', '+1-555-2010', '482 Ash St', 'Boston', 'MA', '02101', '1314'),
    ('Katherine Lee', 'katherine.l@shop.com', '+1-555-2011', '573 Hickory Ct', 'San Francisco', 'CA', '94101', '1516'),
    ('Liam O''Brien', 'liam.ob@shop.com', '+1-555-2012', '684 Sycamore Rd', 'Philadelphia', 'PA', '19101', '1718'),
    ('Maya Singh', 'maya.s@shop.com', '+1-555-2013', '795 Poplar Ln', 'Dallas', 'TX', '75201', '1920'),
    ('Nathan Brooks', 'nathan.b@shop.com', '+1-555-2014', '906 Willow Dr', 'Phoenix', 'AZ', '85001', '2122'),
    ('Olivia Murphy', 'olivia.m@shop.com', '+1-555-2015', '117 Magnolia Ave', 'San Diego', 'CA', '92101', '2324');

INSERT INTO orders (customer_email, product_id, quantity, total_amount, order_date, shipping_address) VALUES
    ('alice@shop.com', 1, 2, 59.98, '2025-06-01 10:30:00', '123 Main St, New York, NY'),
    ('bob@shop.com', 3, 1, 399.99, '2025-06-02 14:00:00', '456 Oak Ave, Los Angeles, CA'),
    ('carol@shop.com', 2, 3, 149.97, '2025-06-03 09:15:00', '789 Pine Rd, Houston, TX'),
    ('alice@shop.com', 5, 1, 129.99, '2025-06-04 16:45:00', '123 Main St, New York, NY'),
    ('david.kim@shop.com', 6, 1, 249.99, '2025-06-05 11:20:00', '321 Elm Blvd, Seattle, WA'),
    ('elena.r@shop.com', 7, 1, 599.99, '2025-06-06 13:30:00', '654 Maple Dr, Miami, FL'),
    ('frank.o@shop.com', 8, 2, 359.98, '2025-06-07 10:00:00', '987 Cedar Ln, Atlanta, GA'),
    ('grace.p@shop.com', 4, 1, 89.99, '2025-06-08 15:45:00', '147 Birch Ct, Chicago, IL'),
    ('henry.n@shop.com', 10, 3, 74.97, '2025-06-09 08:30:00', '258 Walnut Way, Portland, OR'),
    ('isabella.s@shop.com', 14, 1, 129.99, '2025-06-10 12:15:00', '369 Spruce Ave, Denver, CO'),
    ('james.w@shop.com', 15, 1, 89.99, '2025-06-11 09:00:00', '482 Ash St, Boston, MA'),
    ('katherine.l@shop.com', 9, 2, 79.98, '2025-06-12 14:30:00', '573 Hickory Ct, San Francisco, CA'),
    ('liam.ob@shop.com', 20, 1, 199.99, '2025-06-13 11:00:00', '684 Sycamore Rd, Philadelphia, PA'),
    ('maya.s@shop.com', 12, 5, 99.95, '2025-06-14 16:00:00', '795 Poplar Ln, Dallas, TX'),
    ('nathan.b@shop.com', 11, 1, 49.99, '2025-06-15 10:30:00', '906 Willow Dr, Phoenix, AZ'),
    ('olivia.m@shop.com', 16, 2, 89.98, '2025-06-16 13:00:00', '117 Magnolia Ave, San Diego, CA'),
    ('alice@shop.com', 17, 1, 29.99, '2025-06-17 09:45:00', '123 Main St, New York, NY'),
    ('bob@shop.com', 19, 2, 79.98, '2025-06-18 14:15:00', '456 Oak Ave, Los Angeles, CA'),
    ('carol@shop.com', 13, 1, 34.99, '2025-06-19 11:30:00', '789 Pine Rd, Houston, TX'),
    ('david.kim@shop.com', 18, 1, 34.99, '2025-06-20 08:00:00', '321 Elm Blvd, Seattle, WA'),
    ('elena.r@shop.com', 6, 1, 249.99, '2025-06-21 15:30:00', '654 Maple Dr, Miami, FL'),
    ('frank.o@shop.com', 10, 4, 99.96, '2025-06-22 10:15:00', '987 Cedar Ln, Atlanta, GA'),
    ('grace.p@shop.com', 5, 1, 129.99, '2025-06-23 12:00:00', '147 Birch Ct, Chicago, IL'),
    ('henry.n@shop.com', 4, 2, 179.98, '2025-06-24 09:30:00', '258 Walnut Way, Portland, OR'),
    ('james.w@shop.com', 2, 1, 49.99, '2025-06-25 14:45:00', '482 Ash St, Boston, MA');