from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
from werkzeug.security import generate_password_hash, check_password_hash
import pyodbc
from datetime import datetime, timedelta
import csv
import io
import os
from dotenv import load_dotenv

# Load the environment variables from the local .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_only_change_me")
APP_NAME = "Portfolio Pipeline OS"
EXPORT_PREFIX = "portfolio_pipeline"

# =========================
# GLOBAL CRM DROPDOWNS
# =========================
CRM_DROPDOWNS = {
    "sources": ["Website", "Event", "Referral", "LinkedIn", "Cold Outreach", "Partner", "Research", "Portfolio Project", "Email Outreach", "Trade Mission"],
    "lead_categories": ["Investor", "Trade Partner", "Corporate", "SME", "Government", "Event Participant", "Strategic Partner"],
    "sectors": ["Agriculture", "Energy", "Maritime", "Solid minerals"],
    "nature_of_inquiries": ["Trade", "Investment", "Advisory", "Partnership", "Event"],
    "executives": ["CEO", "COO", "Head Of Unit"],
    "lead_types": ["Investor", "Investee", "Partner", "Off-taker", "Supplier", "Donor", "Delegate", "Sponsor", "Exhibitor", "DFIs", "Speaker", "PPP Partner", "Research client", "Co-workspace", "International Development Agency"],
    "pipeline_stages": ["Intro", "Needs Definition", "Proposal Submitted", "DD", "Negotiation", "Verbal Commit", "Closed Won", "Closed Lost"],
    "trade_stages": ["Pre-Execution", "Procurement", "Documentation", "Customs", "In Transit", "Delivered"],
    "business_units": ["Trade", "Capital", "Research", "Portfolio Events"]
}

@app.context_processor
def inject_dropdowns():
    return dict(dropdowns=CRM_DROPDOWNS, app_name=APP_NAME)

# =========================
# CLOUD DATABASE CONNECTION
# =========================
required_env = ["DB_SERVER", "DB_NAME", "DB_UID", "DB_PWD"]
missing_env = [name for name in required_env if not os.getenv(name)]
db_config_error = f"Missing required database environment variables: {', '.join(missing_env)}" if missing_env else None

conn_str = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={os.getenv('DB_SERVER')},1433;"
    f"DATABASE={os.getenv('DB_NAME')};"
    f"UID={os.getenv('DB_UID')};"
    f"PWD={os.getenv('DB_PWD')};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=8;"
)
conn = None
cursor = None
last_db_error = None

def connect_db():
    global conn, cursor, last_db_error
    if db_config_error:
        last_db_error = db_config_error
        return False

    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()
        last_db_error = None
        return True
    except pyodbc.Error as exc:
        conn = None
        cursor = None
        last_db_error = str(exc)
        return False

def database_ready():
    if conn is not None and cursor is not None:
        return True
    return connect_db()

def row_to_dict(cursor, row):
    if not row: return None
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))

def rows_to_dicts(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def normalize_table_name(table):
    return table if table in {"inquiries", "leads", "opportunities"} else "opportunities"

@app.before_request
def ensure_database_connection():
    if request.endpoint == "static":
        return None
    if not database_ready():
        return render_template("db_unavailable.html", error=last_db_error), 503

# =========================
# AUTHENTICATION & REGISTRATION
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        
        cursor.execute("SELECT id, full_name, role, password FROM users WHERE email = ?", email)
        user = cursor.fetchone()
        
        if user and check_password_hash(user[3], password):
            if user[2] == 'Pending':
                return render_template("login.html", error="Your account is pending approval by the Data Manager.")
            
            session["user_id"] = user[0]
            session["name"] = user[1]
            session["role"] = user[2]
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid email or password.")
            
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        try:
            user_name = request.form.get("name")
            user_email = request.form.get("email")
            user_password = request.form.get("password")
            
            if not user_name or not user_email or not user_password:
                return render_template("register.html", error="Please fill out all fields completely.")

            hashed_pw = generate_password_hash(user_password)

            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = cursor.fetchone()[0]
            role = "Data Manager" if user_count == 0 else "Pending"
            
            cursor.execute(
                "INSERT INTO users (full_name, email, password, role) VALUES (?, ?, ?, ?)",
                user_name,
                user_email,
                hashed_pw,
                role,
            )
            conn.commit()
            if role == "Data Manager":
                return render_template("login.html", error="Admin account created. You can sign in now.")
            return render_template("login.html", error="Registration successful! Waiting for Data Manager approval.")
            
        except pyodbc.IntegrityError:
            return render_template("register.html", error="That email is already registered.")
        except Exception as e:
            print(f"DATABASE ERROR: {e}") 
            return render_template("register.html", error="An error occurred. Please try again.")
            
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.before_request
def require_login():
    allowed_routes = ['login', 'register', 'static']
    if request.endpoint not in allowed_routes and 'user_id' not in session:
        return redirect(url_for('login'))

# =========================
# DATA MANAGER: ROLE ASSIGNMENT
# =========================
@app.route("/manage_users", methods=["GET", "POST"])
def manage_users():
    if session.get("role") != "Data Manager": return "Unauthorized Access", 403

    if request.method == "POST":
        cursor.execute("UPDATE users SET role = ? WHERE id = ?", request.form["new_role"], request.form["user_id"])
        conn.commit()
        return redirect(url_for("manage_users"))

    cursor.execute("SELECT id, full_name, email, role FROM users ORDER BY role, full_name")
    users = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
    return render_template("manage_users.html", users=users)

# =========================
# 1. DYNAMIC BI DASHBOARD
# =========================
@app.route("/")
def dashboard():
    # 1. Capture View and Filters
    current_view = request.args.get("view", "opportunities")
    country_f = request.args.get("country", "All")
    bu_f = request.args.get("bu", "All")
    type_f = request.args.get("lead_type", "All")
    status_f = request.args.get("status", "All")

    # Fetch Countries for Dropdown
    cursor.execute("SELECT DISTINCT country FROM inquiries WHERE country IS NOT NULL UNION SELECT DISTINCT country FROM leads WHERE country IS NOT NULL")
    country_list = sorted([r[0] for r in cursor.fetchall()])

    # --- BUILD DYNAMIC WHERE CLAUSES FOR EVERY LAYER ---
    
    # Inquiries Filters
    inq_where = "1=1"
    inq_params = []
    if country_f != "All": inq_where += " AND country = ?"; inq_params.append(country_f)
    
    # Leads Filters
    lead_where = "l.qualification_status != 'Disqualified'"
    lead_params = []
    if country_f != "All": lead_where += " AND l.country = ?"; lead_params.append(country_f)
    if bu_f != "All": lead_where += " AND l.assigned_business_unit = ?"; lead_params.append(bu_f)
    if type_f != "All": lead_where += " AND l.lead_type = ?"; lead_params.append(type_f)
    if current_view == "leads" and status_f != "All": 
        lead_where += " AND l.qualification_status = ?"; lead_params.append(status_f)

    # Opportunities Filters
    opp_where = "1=1"
    opp_params = []
    if country_f != "All": opp_where += " AND o.country = ?"; opp_params.append(country_f)
    if bu_f != "All": opp_where += " AND l.assigned_business_unit = ?"; opp_params.append(bu_f)
    if type_f != "All": opp_where += " AND l.lead_type = ?"; opp_params.append(type_f)
    if current_view == "opportunities" and status_f != "All": 
        opp_where += " AND o.pipeline_stage = ?"; opp_params.append(status_f)

    # 2. FULLY RESPONSIVE FUNNEL COUNTS
    inquiry_count = cursor.execute(f"SELECT COUNT(*) FROM inquiries WHERE {inq_where}", inq_params).fetchone()[0]
    lead_count = cursor.execute(f"SELECT COUNT(*) FROM leads l WHERE {lead_where}", lead_params).fetchone()[0]
    opportunity_count = cursor.execute(f"SELECT COUNT(*) FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {opp_where} AND o.pipeline_stage NOT IN ('Closed Won', 'Closed Lost')", opp_params).fetchone()[0]
    closed_won_count = cursor.execute(f"SELECT COUNT(*) FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {opp_where} AND o.pipeline_stage = 'Closed Won'", opp_params).fetchone()[0]
    booked_rev = cursor.execute(f"SELECT ISNULL(SUM(o.actual_revenue), 0) FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {opp_where} AND o.pipeline_stage = 'Closed Won'", opp_params).fetchone()
    booked_revenue = booked_rev[0] if booked_rev and booked_rev[0] else 0

    # 3. DYNAMIC ANALYTICS STRIP & CHARTS
    if current_view == "inquiries":
        metric_label = "Total Value"
        chart_label = "Inquiries"
        
        cursor.execute(f"SELECT ISNULL(SUM(estimated_opportunity_value), 0) FROM inquiries WHERE {inq_where}", inq_params)
        total_pipeline_value = cursor.fetchone()[0]
        total_est_revenue = 0 

        cursor.execute(f"SELECT TOP 1 logged_by, ISNULL(SUM(estimated_opportunity_value), 0) as metric FROM inquiries WHERE {inq_where} GROUP BY logged_by ORDER BY metric DESC", inq_params)
        top_owner_row = cursor.fetchone()
        
        cursor.execute(f"SELECT TOP 1 sector, ISNULL(SUM(estimated_opportunity_value), 0) as metric FROM inquiries WHERE {inq_where} AND sector != '' GROUP BY sector ORDER BY metric DESC", inq_params)
        top_sector_row = cursor.fetchone()

        cursor.execute(f"SELECT TOP 1 organization_name, ISNULL(SUM(estimated_opportunity_value), 0) as metric FROM inquiries WHERE {inq_where} GROUP BY organization_name ORDER BY metric DESC", inq_params)
        top_client_row = cursor.fetchone()

        cursor.execute(f"SELECT ISNULL(sector, 'Unassigned'), COUNT(*) FROM inquiries WHERE {inq_where} AND sector != '' GROUP BY sector", inq_params)
        sector_rows = cursor.fetchall()

    elif current_view == "leads":
        metric_label = "Est. Lead Value"
        chart_label = "Est. Value"
        
        cursor.execute(f"SELECT ISNULL(SUM(l.estimated_value), 0) FROM leads l WHERE {lead_where}", lead_params)
        total_pipeline_value = cursor.fetchone()[0]
        total_est_revenue = 0

        cursor.execute(f"SELECT TOP 1 l.lead_owner, ISNULL(SUM(l.estimated_value), 0) as metric FROM leads l WHERE {lead_where} GROUP BY l.lead_owner ORDER BY metric DESC", lead_params)
        top_owner_row = cursor.fetchone()
        
        cursor.execute(f"SELECT TOP 1 l.sector, ISNULL(SUM(l.estimated_value), 0) as metric FROM leads l WHERE {lead_where} AND l.sector != '' GROUP BY l.sector ORDER BY metric DESC", lead_params)
        top_sector_row = cursor.fetchone()

        cursor.execute(f"SELECT TOP 1 i.organization_name, ISNULL(SUM(l.estimated_value), 0) as metric FROM leads l JOIN inquiries i ON l.inquiry_id = i.id WHERE {lead_where} GROUP BY i.organization_name ORDER BY metric DESC", lead_params)
        top_client_row = cursor.fetchone()

        cursor.execute(f"SELECT ISNULL(l.sector, 'Unassigned'), ISNULL(SUM(l.estimated_value), 0) FROM leads l WHERE {lead_where} AND l.sector != '' GROUP BY l.sector", lead_params)
        sector_rows = cursor.fetchall()

    else: # Default: opportunities
        metric_label = "Projected Revenue"
        chart_label = "Revenue"
        
        card_where = f"{opp_where} AND o.pipeline_stage NOT IN ('Closed Won', 'Closed Lost')"
        
        cursor.execute(f"SELECT ISNULL(SUM(o.estimated_deal_value), 0), ISNULL(SUM(o.estimated_revenue), 0) FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {card_where}", opp_params)
        fin_row = cursor.fetchone()
        total_pipeline_value, total_est_revenue = (fin_row[0], fin_row[1]) if fin_row else (0, 0)

        cursor.execute(f"SELECT TOP 1 o.primary_owner, ISNULL(SUM(o.estimated_revenue), 0) as metric FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {card_where} GROUP BY o.primary_owner ORDER BY metric DESC", opp_params)
        top_owner_row = cursor.fetchone()

        cursor.execute(f"SELECT TOP 1 o.sector, ISNULL(SUM(o.estimated_revenue), 0) as metric FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {card_where} AND o.sector != '' GROUP BY o.sector ORDER BY metric DESC", opp_params)
        top_sector_row = cursor.fetchone()

        cursor.execute(f"SELECT TOP 1 i.organization_name, ISNULL(SUM(o.estimated_revenue), 0) as metric FROM opportunities o JOIN leads l ON o.lead_id = l.id JOIN inquiries i ON l.inquiry_id = i.id WHERE {card_where} GROUP BY i.organization_name ORDER BY metric DESC", opp_params)
        top_client_row = cursor.fetchone()

        cursor.execute(f"SELECT ISNULL(o.sector, 'Unassigned'), ISNULL(SUM(o.estimated_revenue), 0) FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {card_where} AND o.sector != '' GROUP BY o.sector", opp_params)
        sector_rows = cursor.fetchall()

    top_owner_name, top_owner_metric = (top_owner_row[0], top_owner_row[1]) if top_owner_row else ("N/A", 0)
    top_sector_name, top_sector_metric = (top_sector_row[0], top_sector_row[1]) if top_sector_row else ("N/A", 0)
    top_client_name, top_client_metric = (top_client_row[0], top_client_row[1]) if top_client_row else ("N/A", 0)

    chart_sectors = [r[0] for r in sector_rows] if sector_rows else []
    chart_data = [float(r[1]) for r in sector_rows] if sector_rows else []

    # 4. FULLY RESPONSIVE TABLES DATA
    cursor.execute(f"SELECT o.id, o.opportunity_name, o.pipeline_stage, o.last_interaction_date, o.next_action, o.next_action_date FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {opp_where} AND o.pipeline_stage NOT IN ('Closed Won', 'Closed Lost') ORDER BY o.next_action_date ASC", opp_params)
    active_deals = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    cursor.execute(f"SELECT o.id, o.opportunity_name, o.pipeline_stage, o.actual_close_date, o.actual_deal_value FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE {opp_where} AND o.pipeline_stage IN ('Closed Won', 'Closed Lost') ORDER BY o.actual_close_date DESC", opp_params)
    closed_deals = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    cursor.execute(f"""
        SELECT l.id, i.organization_name, l.qualification_status, l.sector, l.next_action_date 
        FROM leads l JOIN inquiries i ON l.inquiry_id = i.id 
        WHERE {lead_where} AND l.id NOT IN (SELECT lead_id FROM opportunities)
        ORDER BY l.next_action_date ASC
    """, lead_params)
    active_leads = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    stagnancy_threshold = datetime.now() - timedelta(days=7)

    return render_template("dashboard.html", 
                           current_view=current_view, metric_label=metric_label, chart_label=chart_label,
                           country_list=country_list, selected_country=country_f, selected_bu=bu_f, selected_type=type_f, selected_status=status_f,
                           inquiry_count=inquiry_count, lead_count=lead_count, opportunity_count=opportunity_count, closed_won_count=closed_won_count,
                           total_pipeline_value=total_pipeline_value, total_est_revenue=total_est_revenue, booked_revenue=booked_revenue,
                           top_owner_name=top_owner_name, top_owner_metric=top_owner_metric, 
                           top_sector_name=top_sector_name, top_sector_metric=top_sector_metric,
                           top_client_name=top_client_name, top_client_metric=top_client_metric,
                           chart_sectors=chart_sectors, chart_data=chart_data,
                           active_deals=active_deals, closed_deals=closed_deals, active_leads=active_leads,
                           threshold=stagnancy_threshold, today=datetime.now().strftime('%Y-%m-%d'))
# =========================
# GLOBAL SEARCH
# =========================
@app.route("/search")
def search():
    query = request.args.get("q", "")
    search_term = f"%{query}%"

    cursor.execute("""
        SELECT o.id, o.opportunity_name as title, 'Opportunity' as type, o.pipeline_stage as status 
        FROM opportunities o
        JOIN leads l ON o.lead_id = l.id
        JOIN inquiries i ON l.inquiry_id = i.id
        WHERE o.opportunity_name LIKE ? OR i.organization_name LIKE ?
    """, search_term, search_term)
    opp_results = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT l.id, i.organization_name as title, 'Lead' as type, l.qualification_status as status 
        FROM leads l
        JOIN inquiries i ON l.inquiry_id = i.id
        WHERE i.organization_name LIKE ? OR i.contact_name LIKE ?
    """, search_term, search_term)
    lead_results = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]

    results = opp_results + lead_results

    return render_template("search_results.html", query=query, results=results)

# =========================
# REPORTS & EXPORT
# =========================
@app.route("/reports")
def reports():
    table = normalize_table_name(request.args.get("table", "opportunities"))
    sector = request.args.get("sector", "All")
    owner = request.args.get("owner", "All")
    country = request.args.get("country", "All")
    bu = request.args.get("bu", "All")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    params = []
    
    if table == "inquiries":
        query = "SELECT id, organization_name, contact_name, sector, country, lead_category, estimated_opportunity_value, logged_by FROM inquiries WHERE 1=1"
        date_col = "date_logged" 
    elif table == "leads":
        query = "SELECT l.id, i.organization_name, l.sector, l.country, l.assigned_business_unit, l.qualification_status, l.estimated_value, l.lead_owner FROM leads l JOIN inquiries i ON l.inquiry_id = i.id WHERE 1=1"
        date_col = "l.last_contact_date"
    else: 
        query = "SELECT o.id, o.opportunity_name, o.sector, o.country, o.pipeline_stage, o.estimated_deal_value, o.estimated_revenue, o.primary_owner FROM opportunities o JOIN leads l ON o.lead_id = l.id WHERE 1=1"
        date_col = "o.last_interaction_date"

    if sector != "All":
        query += f" AND {'sector' if table == 'inquiries' else 'l.sector' if table == 'leads' else 'o.sector'} = ?"
        params.append(sector)
    
    if owner != "All":
        query += f" AND {'logged_by' if table == 'inquiries' else 'l.lead_owner' if table == 'leads' else 'o.primary_owner'} = ?"
        params.append(owner)

    if country != "All":
        query += f" AND {'country' if table == 'inquiries' else 'l.country' if table == 'leads' else 'o.country'} = ?"
        params.append(country)

    if bu != "All" and table != "inquiries": 
        query += f" AND l.assigned_business_unit = ?"
        params.append(bu)

    if start_date and end_date:
        query += f" AND {date_col} BETWEEN ? AND ?"
        params.append(start_date)
        params.append(end_date)

    cursor.execute(query, params)
    dataset = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
    
    cursor.execute("SELECT DISTINCT full_name FROM users")
    user_list = [r[0] for r in cursor.fetchall()]
    
    cursor.execute("SELECT DISTINCT country FROM leads WHERE country IS NOT NULL")
    country_list = [r[0] for r in cursor.fetchall()]

    return render_template("reports.html", dataset=dataset, selected_table=table, 
                           selected_sector=sector, selected_owner=owner, selected_country=country, 
                           selected_bu=bu, start_date=start_date, end_date=end_date,
                           user_list=user_list, country_list=country_list)

@app.route("/export_csv")
def export_csv():
    table = normalize_table_name(request.args.get("table", "opportunities"))
    sector = request.args.get("sector", "All")
    owner = request.args.get("owner", "All")
    country = request.args.get("country", "All")
    bu = request.args.get("bu", "All")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    params = []
    
    if table == "inquiries":
        headers = ['ID', 'Organization', 'Contact', 'Sector', 'Country', 'Category', 'Est. Value', 'Logged By']
        query = "SELECT id, organization_name, contact_name, sector, country, lead_category, estimated_opportunity_value, logged_by FROM inquiries WHERE 1=1"
        date_col = "date_logged" 
    elif table == "leads":
        headers = ['ID', 'Organization', 'Sector', 'Country', 'Business Unit', 'Status', 'Est. Value', 'Lead Owner']
        query = "SELECT l.id, i.organization_name, l.sector, l.country, l.assigned_business_unit, l.qualification_status, l.estimated_value, l.lead_owner FROM leads l JOIN inquiries i ON l.inquiry_id = i.id WHERE 1=1"
        date_col = "l.last_contact_date"
    else: 
        headers = [
            'Deal/Mandate Name', 'Commodity/Service', 
            'Buyer Name', 'Buyer Country (Destination)', 'Buyer Contact', 
            'Supplier/Counterparty Name', 'Supplier Contact', 
            'Quantity/Volume', 'Est. Value ($)', 'Origin Country', 
            'Current Stage', 'ETD', 'ETA', 
            'Record Owner', 'Date Last Updated', 'Next Action', 'Blockers/Notes'
        ]
        query = """
            SELECT 
                o.opportunity_name, 
                ISNULL(o.commodity, o.sector), 
                i.organization_name, 
                o.country, 
                i.contact_name, 
                o.supplier_name, 
                o.supplier_contact, 
                o.trade_volume, 
                o.estimated_deal_value, 
                o.trade_origin, 
                ISNULL(o.trade_stage, o.pipeline_stage), 
                o.etd, 
                o.eta, 
                o.primary_owner, 
                o.last_interaction_date, 
                o.next_action, 
                o.risk_summary 
            FROM opportunities o 
            JOIN leads l ON o.lead_id = l.id 
            JOIN inquiries i ON l.inquiry_id = i.id 
            WHERE 1=1
        """
        date_col = "o.last_interaction_date"

    if sector != "All":
        query += f" AND {'sector' if table == 'inquiries' else 'l.sector' if table == 'leads' else 'o.sector'} = ?"
        params.append(sector)
    
    if owner != "All":
        query += f" AND {'logged_by' if table == 'inquiries' else 'l.lead_owner' if table == 'leads' else 'o.primary_owner'} = ?"
        params.append(owner)

    if country != "All":
        query += f" AND {'country' if table == 'inquiries' else 'l.country' if table == 'leads' else 'o.country'} = ?"
        params.append(country)

    if bu != "All" and table != "inquiries": 
        query += f" AND l.assigned_business_unit = ?"
        params.append(bu)

    if start_date and end_date:
        query += f" AND {date_col} BETWEEN ? AND ?"
        params.append(start_date)
        params.append(end_date)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
        
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={EXPORT_PREFIX}_{table}.csv"}
    )
# =========================
# 2. INQUIRY INTAKE (WITH DUPLICATE PREVENTION)
# =========================
@app.route("/inquiry", methods=["GET", "POST"])
def inquiry():
    if request.method == "POST":
        try:
            org_name = request.form["organization"].strip()
            email_address = request.form["email"].strip()

            # DUPLICATE CHECK: Does this Email or Org already exist?
            cursor.execute("SELECT id FROM inquiries WHERE email = ? OR organization_name = ?", email_address, org_name)
            if cursor.fetchone():
                return render_template("inquiry_form.html", error=f"Duplicate Alert: An inquiry for '{org_name}' or email '{email_address}' already exists in the system.")

            est_value = request.form.get("value") or 0
            cursor.execute("""
                INSERT INTO inquiries
                (organization_name, contact_name, role_title, email, phone, inquiry_source, lead_category, sector, commodity, country, geography, nature_of_inquiry, estimated_opportunity_value, reputational_risk, logged_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, org_name, request.form["contact"], request.form.get("role", ""), email_address, request.form.get("phone", ""), request.form["source"], request.form["category"], request.form.get("sector", ""), request.form.get("commodity", ""), request.form["country"], request.form["geography"], request.form["nature"], est_value, 1 if "risk_flag" in request.form else 0, session.get("name"))
            conn.commit()
            return redirect(url_for("dashboard"))
        except Exception as e: return f"Error logging inquiry: {e}"
    return render_template("inquiry_form.html")

# =========================
# 3. CREATE LEAD (WITH DUPLICATE PREVENTION & FULL DATA SYNC)
# =========================
@app.route("/lead", methods=["GET", "POST"])
def lead():
    if request.method == "GET":
        cursor.execute("SELECT id, inquiry_code, organization_name, contact_name, estimated_opportunity_value, sector, commodity FROM inquiries WHERE id NOT IN (SELECT inquiry_id FROM leads) ORDER BY date_logged DESC")
        rows = cursor.fetchall()
        open_inquiries = [dict(zip([column[0] for column in cursor.description], row)) for row in rows] if rows else []
        return render_template("lead_form.html", open_inquiries=open_inquiries)

    if request.method == "POST":
        inquiry_id = request.form.get("inquiry_id")
        
        # Grab the Country and Sector from the form so we can apply it everywhere
        lead_country = request.form.get("country", "")
        lead_sector = request.form.get("sector", "")
        lead_commodity = request.form.get("commodity", "")
        
        # If they used Direct Entry (dropdown was empty)
        if not inquiry_id:
            new_org = request.form["new_org"].strip()
            new_email = request.form["new_email"].strip()
            new_contact = request.form["new_contact"].strip()

            # DUPLICATE CHECK FOR DIRECT ENTRY
            cursor.execute("SELECT id FROM inquiries WHERE email = ? OR organization_name = ?", new_email, new_org)
            if cursor.fetchone():
                # Re-fetch the dropdown data so the page doesn't crash when we return the error
                cursor.execute("SELECT id, inquiry_code, organization_name, contact_name, estimated_opportunity_value, sector, commodity FROM inquiries WHERE id NOT IN (SELECT inquiry_id FROM leads) ORDER BY date_logged DESC")
                open_inquiries = [dict(zip([column[0] for column in cursor.description], row)) for row in cursor.fetchall()]
                return render_template("lead_form.html", open_inquiries=open_inquiries, error=f"Duplicate Alert: '{new_org}' or '{new_email}' already exists. Please use the dropdown or search the CRM.")

            # CREATE THE UNDERLYING INQUIRY (Now capturing Country, Sector, and Commodity!)
            cursor.execute("""
                INSERT INTO inquiries (organization_name, contact_name, email, lead_category, inquiry_source, country, sector, commodity, logged_by) 
                OUTPUT INSERTED.id
                VALUES (?, ?, ?, 'Direct Entry', 'Direct Entry', ?, ?, ?, ?)
            """, new_org, new_contact, new_email, lead_country, lead_sector, lead_commodity, session.get("name"))
            inquiry_id = cursor.fetchone()[0]

        # Proceed to create the lead...
        try:
            cursor.execute("""
                INSERT INTO leads
                (inquiry_id, country, sector, commodity, score_financial, score_strategic, score_urgency, score_relationship, score_geographic, lead_owner, assigned_business_unit, event_name, executive_sponsor, lead_type, qualification_status, initial_risk_level, disqualification_reason, budget_confirmed, decision_maker_identified, timeline_defined, last_contact_date, next_action_date, estimated_value, priority)
                OUTPUT INSERTED.ID
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, inquiry_id, lead_country, lead_sector, lead_commodity, request.form["score_financial"], request.form["score_strategic"], request.form["score_urgency"], request.form["score_relationship"], request.form["score_geographic"], request.form["lead_owner"], request.form["business_unit"], request.form.get("event_name", ""), request.form["executive_sponsor"], request.form["lead_type"], request.form["qualification_status"], request.form["risk_level"], request.form.get("disqualification_reason", ""), 1 if "budget_confirmed" in request.form else 0, 1 if "decision_maker" in request.form else 0, 1 if "timeline_defined" in request.form else 0, request.form["last_contact_date"] or None, request.form["next_action_date"] or None, request.form["estimated_value"] or 0, request.form["priority"])
            
            new_lead_id = cursor.fetchone()[0]
            conn.commit()
            
            if request.form["qualification_status"] == "Qualified": 
                return redirect(url_for("opportunity", prefill_id=new_lead_id))
            return redirect(url_for("dashboard"))
            
        except Exception as e: 
            return f"Error creating lead: {e}"
        
# =========================
# 4. CREATE OPPORTUNITY 
# =========================
@app.route("/opportunity", methods=["GET", "POST"])
def opportunity():
    if request.method == "GET":
        prefill_id = request.args.get("prefill_id")
        cursor.execute("SELECT l.id, l.lead_code, i.organization_name, i.contact_name, l.sector, l.commodity FROM leads l JOIN inquiries i ON l.inquiry_id = i.id WHERE l.qualification_status = 'Qualified' AND l.id NOT IN (SELECT lead_id FROM opportunities)")
        qualified_leads = [{"id": r[0], "code": r[1], "org": r[2], "contact": r[3], "sector": r[4], "commodity": r[5]} for r in cursor.fetchall()]
        return render_template("opportunity_form.html", qualified_leads=qualified_leads, prefill_id=prefill_id)

    if request.method == "POST":
        try:
            cursor.execute("""
                INSERT INTO opportunities
                (lead_id, opportunity_name, country, sector, commodity, opportunity_type, event_name, revenue_model, estimated_deal_value, estimated_revenue, estimated_cost, probability, expected_close_date, pipeline_stage, primary_owner, supporting_teams, next_action, next_action_date, risk_summary, go_no_go_required, last_interaction_date, supplier_name, supplier_contact, supplier_phone, supplier_email, trade_origin, trade_volume, trade_stage, etd, eta)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, GETDATE(), ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, request.form["lead_id"], request.form["opportunity_name"], request.form["country"], request.form.get("sector", ""), request.form.get("commodity", ""), request.form["type"], request.form.get("event_name", ""), request.form["revenue_model"], request.form["deal_value"] or 0, request.form["revenue"] or 0, request.form["cost"] or 0, request.form["probability"], request.form["close_date"], request.form["stage"], request.form["owner"], request.form["support"], request.form["next_action"], request.form["next_action_date"], request.form["risk_summary"],
            request.form.get("supplier_name", ""), request.form.get("supplier_contact", ""), request.form.get("supplier_phone", ""), request.form.get("supplier_email", ""), request.form.get("trade_origin", ""), request.form.get("trade_volume", ""), request.form.get("trade_stage", ""), request.form.get("etd") or None, request.form.get("eta") or None)
            conn.commit()
            return redirect(url_for("dashboard"))
        except Exception as e: return f"Error creating opportunity: {e}"

# =========================
# ESCALATIONS & FLAGS HUB
# =========================
@app.route("/escalations")
def escalations():
    cursor.execute("""
        SELECT e.id as esc_id, o.id as opp_id, o.opportunity_name, o.primary_owner, 
               e.escalation_reason, e.escalated_to
        FROM escalations e
        JOIN opportunities o ON e.opportunity_id = o.id
        ORDER BY e.id DESC
    """)
    esc_rows = cursor.fetchall()
    escalated_deals = [dict(zip([column[0] for column in cursor.description], row)) for row in esc_rows] if esc_rows else []

    cursor.execute("""
        SELECT id, organization_name, contact_name, logged_by, sector 
        FROM inquiries 
        WHERE reputational_risk = 1
        ORDER BY id DESC
    """)
    flag_rows = cursor.fetchall()
    flagged_inquiries = [dict(zip([column[0] for column in cursor.description], row)) for row in flag_rows] if flag_rows else []

    return render_template("escalations.html", escalated_deals=escalated_deals, flagged_inquiries=flagged_inquiries)

# =========================
# LEAD PROFILE & NURTURE HUB
# =========================
@app.route("/lead/<int:id>", methods=["GET", "POST"])
def lead_detail(id):
    if request.method == "POST":
        action = request.form.get("action")
        
        # 1. Handle Status Updates
        if action == "update_status":
            new_status = request.form.get("qualification_status")
            cursor.execute("UPDATE leads SET qualification_status = ? WHERE id = ?", new_status, id)
            conn.commit()
            
        # 2. Handle Lead Detail Edits
        elif action == "update_details":
            cursor.execute("""
                UPDATE leads 
                SET estimated_value = ?, priority = ?, lead_type = ?
                WHERE id = ?
            """, request.form.get("estimated_value", 0), request.form.get("priority"), request.form.get("lead_type"), id)
            conn.commit()
            
        return redirect(url_for("lead_detail", id=id))

    cursor.execute("""
        SELECT l.*, i.organization_name, i.contact_name, i.email, i.phone 
        FROM leads l 
        JOIN inquiries i ON l.inquiry_id = i.id 
        WHERE l.id = ?
    """, id)
    lead_row = cursor.fetchone()
    
    if not lead_row:
        return "Lead not found", 404
        
    lead = dict(zip([column[0] for column in cursor.description], lead_row))

    cursor.execute("""
        SELECT interaction_date, interaction_type, notes, logged_by 
        FROM interactions 
        WHERE lead_id = ? 
        ORDER BY interaction_date DESC
    """, id)
    history_rows = cursor.fetchall()
    interaction_history = [dict(zip([column[0] for column in cursor.description], row)) for row in history_rows] if history_rows else []

    return render_template("lead_detail.html", lead=lead, interaction_history=interaction_history)

# =========================
# EDIT LEAD (FULL UPDATE)
# =========================
@app.route("/lead/<int:id>/edit", methods=["GET", "POST"])
def edit_lead(id):
    if request.method == "POST":
        try:
            cursor.execute("""
                UPDATE leads 
                SET country=?, sector=?, commodity=?, score_financial=?, score_strategic=?, score_urgency=?, score_relationship=?, score_geographic=?, lead_owner=?, assigned_business_unit=?, event_name=?, executive_sponsor=?, lead_type=?, initial_risk_level=?, budget_confirmed=?, decision_maker_identified=?, timeline_defined=?, last_contact_date=?, next_action_date=?, estimated_value=?, priority=?
                WHERE id=?
            """, 
            request.form["country"], request.form.get("sector", ""), request.form.get("commodity", ""), 
            request.form["score_financial"], request.form["score_strategic"], request.form["score_urgency"], 
            request.form["score_relationship"], request.form["score_geographic"], request.form["lead_owner"], 
            request.form["business_unit"], request.form.get("event_name", ""), request.form["executive_sponsor"], 
            request.form["lead_type"], request.form["risk_level"], 
            1 if "budget_confirmed" in request.form else 0, 
            1 if "decision_maker" in request.form else 0, 
            1 if "timeline_defined" in request.form else 0, 
            request.form["last_contact_date"] or None, 
            request.form["next_action_date"] or None, 
            request.form["estimated_value"] or 0, 
            request.form["priority"], 
            id)
            conn.commit()
            return redirect(url_for("lead_detail", id=id))
        except Exception as e:
            return f"Error updating lead: {e}"

    # For GET requests, fetch the existing data to pre-fill the form
    cursor.execute("""
        SELECT l.*, i.organization_name, i.contact_name 
        FROM leads l 
        JOIN inquiries i ON l.inquiry_id = i.id 
        WHERE l.id = ?
    """, id)
    lead_row = cursor.fetchone()
    if not lead_row: return "Lead not found", 404
    
    lead = dict(zip([column[0] for column in cursor.description], lead_row))
    return render_template("edit_lead.html", lead=lead)

# =========================
# OPPORTUNITY HUB (MASTER)
# =========================
@app.route("/opportunity/<int:id>", methods=["GET", "POST"])
def opportunity_detail(id):
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "update_deal":
            cursor.execute("""
                UPDATE opportunities 
                SET pipeline_stage=?, probability=?, estimated_deal_value=?, estimated_revenue=?, last_interaction_date=GETDATE() 
                WHERE id=?
            """, request.form["stage"], request.form["probability"], request.form["deal_value"], request.form["revenue"], id)
            conn.commit()
            
        elif action == "escalate":
            cursor.execute("INSERT INTO escalations (opportunity_id, escalation_reason, escalated_to, escalation_required) VALUES (?, ?, ?, 1)", id, request.form["reason"], request.form["to"])
            conn.commit()

        elif action == "approve_deal":
            if session.get("role") not in ["Data Manager", "Admin", "CEO", "COO"]:
                return "Unauthorized: Only Management can approve deals.", 403
            cursor.execute("INSERT INTO go_no_go_governance (opportunity_id, go_no_go_status, approved_by, review_date) VALUES (?, ?, ?, GETDATE())", id, request.form["status"], session.get("name"))
            conn.commit()

        elif action == "close_won":
            cursor.execute("SELECT TOP 1 go_no_go_status FROM go_no_go_governance WHERE opportunity_id = ? ORDER BY review_date DESC", id)
            gov_check = cursor.fetchone()
            if not gov_check or gov_check[0] != 'Go': 
                return "Framework Violation: Cannot close this opportunity. Management 'Go' approval is required.", 403
            
            cursor.execute("""
                UPDATE opportunities 
                SET pipeline_stage = 'Closed Won', probability = 100, actual_deal_value = ?, actual_revenue = ?, actual_close_date = ?, closed_by = ?, last_interaction_date = GETDATE() 
                WHERE id = ?
            """, request.form["final_deal_value"], request.form["final_revenue"], request.form["final_date"], session.get("name"), id)
            conn.commit()
            
        return redirect(url_for("opportunity_detail", id=id))

    cursor.execute("""
        SELECT o.*, i.organization_name, i.contact_name, l.lead_code, l.country as target_country
        FROM opportunities o
        JOIN leads l ON o.lead_id = l.id
        JOIN inquiries i ON l.inquiry_id = i.id
        WHERE o.id = ?
    """, id)
    deal_row = cursor.fetchone()
    if not deal_row: return "Opportunity not found", 404
    deal = dict(zip([column[0] for column in cursor.description], deal_row))
    
    client = {"org": deal["organization_name"], "contact": deal["contact_name"]}

    cursor.execute("""
        SELECT interaction_date, interaction_type, notes, logged_by 
        FROM interactions 
        WHERE lead_id = ? 
        ORDER BY interaction_date DESC
    """, deal["lead_id"])
    history_rows = cursor.fetchall()
    interaction_history = [dict(zip([column[0] for column in cursor.description], row)) for row in history_rows] if history_rows else []

    cursor.execute("SELECT TOP 1 * FROM go_no_go_governance WHERE opportunity_id = ? ORDER BY review_date DESC", id)
    gov_row = cursor.fetchone()
    governance = dict(zip([column[0] for column in cursor.description], gov_row)) if gov_row else None

    return render_template("opportunity_detail.html", deal=deal, client=client, interaction_history=interaction_history, governance=governance)

# =========================
# EDIT INQUIRY (FULL UPDATE)
# =========================
@app.route("/inquiry/<int:id>/edit", methods=["GET", "POST"])
def edit_inquiry(id):
    if request.method == "POST":
        try:
            cursor.execute("""
                UPDATE inquiries 
                SET organization_name=?, contact_name=?, role_title=?, email=?, phone=?, inquiry_source=?, lead_category=?, sector=?, commodity=?, country=?, geography=?, nature_of_inquiry=?, estimated_opportunity_value=?
                WHERE id=?
            """, request.form["organization"], request.form["contact"], request.form.get("role", ""), request.form["email"], request.form.get("phone", ""), request.form["source"], request.form["category"], request.form.get("sector", ""), request.form.get("commodity", ""), request.form["country"], request.form["geography"], request.form["nature"], request.form.get("value") or 0, id)
            conn.commit()
            return redirect(url_for("reports", table="inquiries"))
        except Exception as e: return f"Error updating inquiry: {e}"

    cursor.execute("SELECT * FROM inquiries WHERE id = ?", id)
    row = cursor.fetchone()
    if not row: return "Inquiry not found", 404
    inquiry = dict(zip([column[0] for column in cursor.description], row))
    return render_template("edit_inquiry.html", inquiry=inquiry)

# =========================
# EDIT OPPORTUNITY (FULL UPDATE - RECONCILED WITH TRADE FIELDS)
# =========================
@app.route("/opportunity/<int:id>/edit", methods=["GET", "POST"])
def edit_opportunity(id):
    if request.method == "POST":
        try:
            cursor.execute("""
                UPDATE opportunities 
                SET opportunity_name=?, country=?, sector=?, commodity=?, opportunity_type=?, event_name=?, revenue_model=?, estimated_deal_value=?, estimated_revenue=?, estimated_cost=?, expected_close_date=?, supporting_teams=?, risk_summary=?, supplier_name=?, supplier_contact=?, supplier_phone=?, supplier_email=?, trade_origin=?, trade_volume=?, trade_stage=?, etd=?, eta=?
                WHERE id=?
            """, request.form["opportunity_name"], request.form["country"], request.form.get("sector", ""), request.form.get("commodity", ""), request.form["type"], request.form.get("event_name", ""), request.form["revenue_model"], request.form["deal_value"] or 0, request.form["revenue"] or 0, request.form["cost"] or 0, request.form["close_date"], request.form["support"], request.form["risk_summary"], request.form.get("supplier_name", ""), request.form.get("supplier_contact", ""), request.form.get("supplier_phone", ""), request.form.get("supplier_email", ""), request.form.get("trade_origin", ""), request.form.get("trade_volume", ""), request.form.get("trade_stage", ""), request.form.get("etd") or None, request.form.get("eta") or None, id)
            conn.commit()
            return redirect(url_for("opportunity_detail", id=id))
        except Exception as e: return f"Error updating opportunity: {e}"

    cursor.execute("SELECT * FROM opportunities WHERE id = ?", id)
    row = cursor.fetchone()
    if not row: return "Opportunity not found", 404
    deal = dict(zip([column[0] for column in cursor.description], row))
    return render_template("edit_opportunity.html", deal=deal)

# =========================
# ACTIVITY & INTERACTION LOGGING
# =========================
@app.route("/add_interaction", methods=["POST"])
def add_interaction():
    lead_id = request.form.get("lead_id")
    interaction_type = request.form.get("interaction_type")
    notes = request.form.get("notes")
    logged_by = session.get("name")
    
    cursor.execute("""
        INSERT INTO interactions (lead_id, interaction_date, interaction_type, notes, logged_by) 
        VALUES (?, GETDATE(), ?, ?, ?)
    """, lead_id, interaction_type, notes, logged_by)
    conn.commit()
    
    return redirect(request.referrer)

if __name__ == "__main__":
    debug_mode = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes"}
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
