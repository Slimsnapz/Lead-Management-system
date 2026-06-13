# Portfolio Pipeline OS

Portfolio Pipeline OS is a Flask + Azure SQL data product for demonstrating end-to-end analytics engineering skills. It models how raw opportunities move through a governed decision funnel: inquiry intake, lead scoring, pipeline management, revenue tracking, risk escalation, and exportable reporting.

## What It Demonstrates

- Cloud SQL schema design with relational constraints.
- Secure-ish Flask authentication with hashed passwords and role approval.
- Funnel analytics across inquiries, leads, opportunities, and closed revenue.
- Operational workflow design: next actions, risk flags, escalation, governance, and CSV export.
- Recruiter-friendly UI focused on data product thinking rather than a generic CRUD app.

## First Run

1. Create `.env` from `.env.example`.
2. Allow your current IP address in Azure SQL Server networking.
3. Initialize the schema:

```powershell
python db_admin.py init
```

4. Start the app:

```powershell
python app.py
```

5. Register the first account in the app. The first registered user automatically becomes `Data Manager`.

## Database Admin Commands

Inspect tables:

```powershell
python db_admin.py inspect
```

Initialize empty database:

```powershell
python db_admin.py init
```

Reset app-owned tables:

```powershell
python db_admin.py reset --yes
```

`reset --yes` drops and recreates only the app tables defined in `db_admin.py`.
