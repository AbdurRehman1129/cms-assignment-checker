import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
def get_env_var(var_name):
    value = os.environ.get(var_name)
    if not value:
        raise ValueError(f"Missing environment variable: {var_name}")
    return value

CMS_URL = "https://cms.must.edu.pk:8082/login.aspx"
ROLL_NO = get_env_var("ROLL_NO")
PASSWORD = get_env_var("PASSWORD")
SESSION = get_env_var("SESSION")
PROGRAM = get_env_var("PROGRAM")

# Store latest HTML table in memory
latest_html_table = {"html": "", "last_updated": ""}

def login_and_get_session():
    session = requests.Session()
    response = session.get(CMS_URL)
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

    response = session.post(CMS_URL, data=data)
    if response.status_code == 200 and "DashBoard.aspx" in response.text:
        logger.info("Login successful.")
        return session
    else:
        logger.error("Login failed.")
        return None

def check_assignments():
    session = login_and_get_session()
    if not session:
        return None
    assignments_url = "https://cms.must.edu.pk:8082/CoursePortal.aspx"
    response = session.get(assignments_url)
    return response

def extract_html_table(response):
    start_marker = '<table class="Grid" cellspacing="0" rules="all" bordercolor="#D8D8D8" border="1" id="ctl00_DataContent_gvPortalSummary"'
    end_marker = '</table>'
    
    start_idx = response.find(start_marker)
    if start_idx == -1:
        return ""
    
    end_idx = response.find(end_marker, start_idx) + len(end_marker)
    if end_idx == -1:
        return ""
    
    return response[start_idx:end_idx]

def remove_submission_column(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    table = soup.find('table')
    
    if not table:
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
                latest_html_table["html"] = modified_html
                latest_html_table["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                logger.info("Assignments fetched and stored.")
            else:
                logger.warning("No HTML table found.")
        else:
            logger.error("Failed to fetch assignments.")
    except Exception as e:
        logger.error(f"Error fetching assignments: {e}")

# Schedule assignment checks every 5 minutes
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_and_store_assignments, 'interval', minutes=5)
scheduler.start()

# Run initial fetch on startup
fetch_and_store_assignments()

@app.route('/api/assignments', methods=['GET'])
def get_assignments():
    return jsonify(latest_html_table)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))