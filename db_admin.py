import argparse
import getpass
import os
import sys

import pyodbc
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


APP_TABLES = [
    "go_no_go_governance",
    "escalations",
    "interactions",
    "opportunities",
    "leads",
    "inquiries",
    "users",
]


def connection_string():
    load_dotenv()
    required = ["DB_SERVER", "DB_NAME", "DB_UID", "DB_PWD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required database environment variables: {', '.join(missing)}")

    return (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={os.getenv('DB_SERVER')},1433;"
        f"DATABASE={os.getenv('DB_NAME')};"
        f"UID={os.getenv('DB_UID')};"
        f"PWD={os.getenv('DB_PWD')};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=15;"
    )


def connect():
    return pyodbc.connect(connection_string(), autocommit=False)


def fetch_tables(cursor):
    cursor.execute(
        """
        SELECT TABLE_SCHEMA, TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_SCHEMA, TABLE_NAME
        """
    )
    return [f"{row.TABLE_SCHEMA}.{row.TABLE_NAME}" for row in cursor.fetchall()]


def table_exists(cursor, table_name):
    cursor.execute("SELECT OBJECT_ID(?)", f"dbo.{table_name}")
    return cursor.fetchone()[0] is not None


def execute_statements(cursor, statements):
    for statement in statements:
        cursor.execute(statement)


def drop_app_tables(cursor):
    for table_name in APP_TABLES:
        if table_exists(cursor, table_name):
            cursor.execute(f"DROP TABLE dbo.{table_name}")


def create_schema(cursor):
    statements = [
        """
        CREATE TABLE dbo.users (
            id INT IDENTITY(1,1) PRIMARY KEY,
            full_name NVARCHAR(150) NOT NULL,
            email NVARCHAR(255) NOT NULL UNIQUE,
            password NVARCHAR(255) NOT NULL,
            role NVARCHAR(50) NOT NULL DEFAULT 'Pending',
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
        )
        """,
        """
        CREATE TABLE dbo.inquiries (
            id INT IDENTITY(1,1) PRIMARY KEY,
            inquiry_code AS ('INQ-' + RIGHT('00000' + CONVERT(VARCHAR(10), id), 5)) PERSISTED,
            organization_name NVARCHAR(255) NOT NULL,
            contact_name NVARCHAR(150) NULL,
            role_title NVARCHAR(150) NULL,
            email NVARCHAR(255) NULL,
            phone NVARCHAR(80) NULL,
            inquiry_source NVARCHAR(100) NULL,
            lead_category NVARCHAR(100) NULL,
            sector NVARCHAR(100) NULL,
            commodity NVARCHAR(150) NULL,
            country NVARCHAR(100) NULL,
            geography NVARCHAR(100) NULL,
            nature_of_inquiry NVARCHAR(100) NULL,
            estimated_opportunity_value DECIMAL(18,2) NOT NULL DEFAULT 0,
            reputational_risk BIT NOT NULL DEFAULT 0,
            logged_by NVARCHAR(150) NULL,
            date_logged DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
        )
        """,
        """
        CREATE TABLE dbo.leads (
            id INT IDENTITY(1,1) PRIMARY KEY,
            lead_code AS ('LED-' + RIGHT('00000' + CONVERT(VARCHAR(10), id), 5)) PERSISTED,
            inquiry_id INT NOT NULL,
            country NVARCHAR(100) NULL,
            sector NVARCHAR(100) NULL,
            commodity NVARCHAR(150) NULL,
            score_financial INT NOT NULL DEFAULT 0,
            score_strategic INT NOT NULL DEFAULT 0,
            score_urgency INT NOT NULL DEFAULT 0,
            score_relationship INT NOT NULL DEFAULT 0,
            score_geographic INT NOT NULL DEFAULT 0,
            lead_owner NVARCHAR(150) NULL,
            assigned_business_unit NVARCHAR(100) NULL,
            event_name NVARCHAR(255) NULL,
            executive_sponsor NVARCHAR(150) NULL,
            lead_type NVARCHAR(100) NULL,
            qualification_status NVARCHAR(80) NOT NULL DEFAULT 'New',
            initial_risk_level NVARCHAR(80) NULL,
            disqualification_reason NVARCHAR(500) NULL,
            budget_confirmed BIT NOT NULL DEFAULT 0,
            decision_maker_identified BIT NOT NULL DEFAULT 0,
            timeline_defined BIT NOT NULL DEFAULT 0,
            last_contact_date DATE NULL,
            next_action_date DATE NULL,
            estimated_value DECIMAL(18,2) NOT NULL DEFAULT 0,
            priority NVARCHAR(50) NULL,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_leads_inquiries FOREIGN KEY (inquiry_id) REFERENCES dbo.inquiries(id)
        )
        """,
        """
        CREATE TABLE dbo.opportunities (
            id INT IDENTITY(1,1) PRIMARY KEY,
            lead_id INT NOT NULL,
            opportunity_name NVARCHAR(255) NOT NULL,
            country NVARCHAR(100) NULL,
            sector NVARCHAR(100) NULL,
            commodity NVARCHAR(150) NULL,
            opportunity_type NVARCHAR(100) NULL,
            event_name NVARCHAR(255) NULL,
            revenue_model NVARCHAR(150) NULL,
            estimated_deal_value DECIMAL(18,2) NOT NULL DEFAULT 0,
            estimated_revenue DECIMAL(18,2) NOT NULL DEFAULT 0,
            estimated_cost DECIMAL(18,2) NOT NULL DEFAULT 0,
            probability INT NOT NULL DEFAULT 0,
            expected_close_date DATE NULL,
            pipeline_stage NVARCHAR(80) NOT NULL DEFAULT 'Intro',
            primary_owner NVARCHAR(150) NULL,
            supporting_teams NVARCHAR(500) NULL,
            next_action NVARCHAR(500) NULL,
            next_action_date DATE NULL,
            risk_summary NVARCHAR(MAX) NULL,
            go_no_go_required BIT NOT NULL DEFAULT 0,
            last_interaction_date DATETIME2 NULL,
            supplier_name NVARCHAR(255) NULL,
            supplier_contact NVARCHAR(150) NULL,
            supplier_phone NVARCHAR(80) NULL,
            supplier_email NVARCHAR(255) NULL,
            trade_origin NVARCHAR(150) NULL,
            trade_volume NVARCHAR(150) NULL,
            trade_stage NVARCHAR(100) NULL,
            etd DATE NULL,
            eta DATE NULL,
            actual_deal_value DECIMAL(18,2) NULL,
            actual_revenue DECIMAL(18,2) NULL,
            actual_close_date DATE NULL,
            closed_by NVARCHAR(150) NULL,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_opportunities_leads FOREIGN KEY (lead_id) REFERENCES dbo.leads(id)
        )
        """,
        """
        CREATE TABLE dbo.interactions (
            id INT IDENTITY(1,1) PRIMARY KEY,
            lead_id INT NOT NULL,
            interaction_date DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            interaction_type NVARCHAR(100) NULL,
            notes NVARCHAR(MAX) NULL,
            logged_by NVARCHAR(150) NULL,
            CONSTRAINT FK_interactions_leads FOREIGN KEY (lead_id) REFERENCES dbo.leads(id)
        )
        """,
        """
        CREATE TABLE dbo.escalations (
            id INT IDENTITY(1,1) PRIMARY KEY,
            opportunity_id INT NOT NULL,
            escalation_reason NVARCHAR(MAX) NULL,
            escalated_to NVARCHAR(150) NULL,
            escalation_required BIT NOT NULL DEFAULT 1,
            created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_escalations_opportunities FOREIGN KEY (opportunity_id) REFERENCES dbo.opportunities(id)
        )
        """,
        """
        CREATE TABLE dbo.go_no_go_governance (
            id INT IDENTITY(1,1) PRIMARY KEY,
            opportunity_id INT NOT NULL,
            go_no_go_status NVARCHAR(30) NOT NULL,
            approved_by NVARCHAR(150) NULL,
            review_date DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT FK_go_no_go_opportunities FOREIGN KEY (opportunity_id) REFERENCES dbo.opportunities(id)
        )
        """,
    ]
    execute_statements(cursor, statements)


def create_admin(cursor, email, password, full_name):
    cursor.execute(
        """
        INSERT INTO dbo.users (full_name, email, password, role)
        VALUES (?, ?, ?, 'Data Manager')
        """,
        full_name,
        email,
        generate_password_hash(password),
    )


def inspect_command(_args):
    with connect() as conn:
        cursor = conn.cursor()
        tables = fetch_tables(cursor)
        print("Connected.")
        print("Tables:")
        for table in tables:
            print(f"- {table}")
        if not tables:
            print("- none")


def init_command(args):
    password = args.admin_password or os.getenv("ADMIN_PASSWORD")

    with connect() as conn:
        cursor = conn.cursor()
        create_schema(cursor)
        if password:
            create_admin(cursor, args.admin_email, password, args.admin_name)
        conn.commit()
        print("Schema initialized.")
        if password:
            print(f"Admin user: {args.admin_email}")
        else:
            print("No admin user was seeded. Register the first account in the app to become Data Manager.")


def reset_command(args):
    if not args.yes:
        print("Refusing to reset without --yes.")
        return 2

    password = args.admin_password or os.getenv("ADMIN_PASSWORD")

    with connect() as conn:
        cursor = conn.cursor()
        drop_app_tables(cursor)
        create_schema(cursor)
        if password:
            create_admin(cursor, args.admin_email, password, args.admin_name)
        conn.commit()
        print("Database reset complete.")
        if password:
            print(f"Admin user: {args.admin_email}")
        else:
            print("No admin user was seeded. Register the first account in the app to become Data Manager.")


def build_parser():
    parser = argparse.ArgumentParser(description="Database admin helper for Portfolio Pipeline OS.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("inspect", help="List current SQL database tables.").set_defaults(func=inspect_command)

    init_parser = subparsers.add_parser("init", help="Create app tables in an empty database.")
    init_parser.add_argument("--admin-email", default=os.getenv("ADMIN_EMAIL", "admin@example.com"))
    init_parser.add_argument("--admin-name", default=os.getenv("ADMIN_NAME", "Portfolio Admin"))
    init_parser.add_argument("--admin-password", default=None)
    init_parser.set_defaults(func=init_command)

    reset_parser = subparsers.add_parser("reset", help="Drop app tables, recreate schema, and create an admin.")
    reset_parser.add_argument("--yes", action="store_true", help="Confirm destructive reset.")
    reset_parser.add_argument("--admin-email", default=os.getenv("ADMIN_EMAIL", "admin@example.com"))
    reset_parser.add_argument("--admin-name", default=os.getenv("ADMIN_NAME", "Portfolio Admin"))
    reset_parser.add_argument("--admin-password", default=None)
    reset_parser.set_defaults(func=reset_command)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    result = args.func(args)
    if result:
        sys.exit(result)


if __name__ == "__main__":
    main()
