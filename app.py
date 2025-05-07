import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import logging
import sys
import pytz

app = Flask(__name__)
# Allow all origins for development, including localhost
CORS(app, resources={r"/api/*": {"origins": ["*", "http://localhost:*", "https://*.vercel.app"]}}, supports_credentials=True)

# Configure logging to stdout for Heroku
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Log incoming requests for debugging CORS
@app.before_request
def log_request():
    logger.info(f"Request: {request.method} {request.path} from {request.origin} with headers {request.headers.get('Origin')}")

# Load environment variables
def get_env_var(var_name):
    value = os.environ.get(var_name)
    if not value:
        logger.error(f"Missing environment variable: {var_name}")
        raise ValueError(f"Missing environment variable: {var_name}")
    return value

try:
    CMS_URL = "https://cms.must.edu.pk:8082/login.aspx"
    ROLL_NO = get_env_var("ROLL_NO")
    PASSWORD = get_env_var("PASSWORD")
    SESSION = get_env_var("SESSION")
    PROGRAM = get_env_var("PROGRAM")
    logger.info("Environment variables loaded successfully")
except Exception as e:
    logger.error(f"Failed to load environment variables: {e}")
    raise

# Store latest HTML table in memory
latest_html_table = {"html": "", "last_updated": ""}

def login_and_get_session():
    session = requests.Session()
    try:
        response = session.get(CMS_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        viewstate = soup.find("input", {"name": "__VIEWSTATE"})["value"] if soup.find("input", {"name": "__VIEWSTATE"}) else ""
        viewstategen = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})["value"] if soup.find("input", {"name": "__VIEWSTATEGENERATOR"}) else ""
        eventvalidation = soup.find("input", {"name": "__EVENTVALIDATION"})["value"] if soup.find("input", {"name": "__EVENTVALIDATION"}) else ""

        data = {
            "__VIEWSTATE": viewstate,
            "__VIEWSTATEGENERATOR": viewstategen,
            "__EVENTVALIDATION": eventvalidation,
            "ddl_Session": SESSION,
            "ddl_Program": PROGRAM,
            "txt_RollNo": ROLL_NO,
            "txt_Password": PASSWORD,
            "btn_StudentSignIn": "Sign In"
        }

        response = session.post(CMS_URL, data=data, timeout=10)
        response.raise_for_status()
        if "DashBoard.aspx" in response.text:
            logger.info("Login successful")
            return session
        else:
            logger.error("Login failed: Dashboard not found in response")
            return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        return None

def check_assignments():
    session = login_and_get_session()
    if not session:
        logger.warning("No session available for assignments check")
        return None
    try:
        assignments_url = "https://cms.must.edu.pk:8082/CoursePortal.aspx"
        response = session.get(assignments_url, timeout=10)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Error fetching assignments: {e}")
        return None

def extract_html_table(response):
    start_marker = '<table class="Grid" cellspacing="0" rules="all" bordercolor="#D8D8D8" border="1" id="ctl00_DataContent_gvPortalSummary"'
    end_marker = '</table>'
    
    start_idx = response.find(start_marker)
    if start_idx == -1:
        logger.warning("Table start marker not found")
        return ""
    
    end_idx = response.find(end_marker, start_idx) + len(end_marker)
    if end_idx == -1:
        logger.warning("Table end marker not found")
        return ""
    
    return response[start_idx:end_idx]

def remove_submission_column(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')
    
    if not table:
        logger.warning("No table found in HTML content")
        return html_content
    
    for th in table.find_all('th', class_='GridHeader'):
        if 'Submission' in th.get_text():
            th.decompose()
    
    for row in table.find_all('tr'):
        cells = row.find_all(['th', 'td'])
        if len(cells) >= 6:
            submission_cell = None
            for cell in cells:
                if 'lblSubmissionStatus' in str(cell):
                    submission_cell = cell
                    break
            if submission_cell:
                submission_cell.decompose()
    
    return str(soup)

def fetch_and_store_assignments():
    global latest_html_table
    try:
        response = check_assignments()
        if response:
            html_table = extract_html_table(response.text)
            if html_table:
                modified_html = remove_submission_column(html_table)
                # Use Pakistan timezone (Asia/Karachi)
                pakistan_tz = pytz.timezone('Asia/Karachi')
                latest_html_table["html"] = modified_html
                latest_html_table["last_updated"] = datetime.now(pakistan_tz).strftime("%Y-%m-%d %H:%M:%S PST")
                logger.info("Assignments fetched and stored")
            else:
                logger.warning("No HTML table extracted")
        else:
            logger.warning("No response from assignments check")
    except Exception as e:
        logger.error(f"Error in fetch_and_store_assignments: {e}")

# Debug endpoint to force a CMS check
@app.route('/api/debug/check', methods=['GET'])
def debug_check():
    logger.info("Manual CMS check triggered")
    fetch_and_store_assignments()
    return jsonify({"status": "Check triggered", "latest_data": latest_html_table})

# Schedule assignment checks every 5 minutes
try:
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_store_assignments, 'interval', minutes=5)
    scheduler.start()
    logger.info("Scheduler started")
except Exception as e:
    logger.error(f"Failed to start scheduler: {e}")

# Run initial fetch on startup
try:
    fetch_and_store_assignments()
    logger.info("Initial assignments fetch completed")
except Exception as e:
    logger.error(f"Initial fetch failed: {e}")

@app.route('/api/assignments', methods=['GET'])
def get_assignments():
    logger.info("Serving /api/assignments request")
    return jsonify(latest_html_table)

@app.route('/', methods=['GET'])
def index():
    logger.info("Serving / request")
    return jsonify({"status": "API is running", "endpoint": "/api/assignments"})

if __name__ == "__main__":
    logger.info("Starting Flask app")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))