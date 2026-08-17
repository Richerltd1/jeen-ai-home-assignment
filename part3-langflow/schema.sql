-- Part 3 -- database used by the Analysis Agent's SQL Tool.
--
-- Table definition and seed rows are exactly as specified in the assignment
-- brief. The indexes below are additions: the Analysis Agent filters on status,
-- priority and category on almost every call, and looks customers up by email
-- before deciding whether information is missing.

CREATE TABLE IF NOT EXISTS support_requests (
    id            SERIAL PRIMARY KEY,
    customer_name VARCHAR(100),
    email         VARCHAR(255),
    category      VARCHAR(100),
    priority      VARCHAR(50),
    status        VARCHAR(50),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO support_requests
    (customer_name, email, category, priority, status)
VALUES
    ('John Smith',    'john@example.com',    'Login Issue',       'High',   'Open'),
    ('Sarah Cohen',   'sarah@example.com',   'Billing',           'Medium', 'In Progress'),
    ('David Levi',    'david@example.com',   'Technical Support', 'Low',    'Closed'),
    ('Emma Johnson',  'emma@example.com',    'Account Access',    'High',   'Open'),
    ('Michael Brown', 'michael@example.com', 'Subscription',      'Medium', 'Open');

CREATE INDEX IF NOT EXISTS support_requests_email_idx    ON support_requests (email);
CREATE INDEX IF NOT EXISTS support_requests_status_idx   ON support_requests (status);
CREATE INDEX IF NOT EXISTS support_requests_category_idx ON support_requests (category);
