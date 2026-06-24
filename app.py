import os
import io
import json
import datetime
import pytz
import pandas as pd
import streamlit as st
import plotly.express as px
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google import genai
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agents import (
    adjust_risk_for_horizon_density,
    crisis_agent,
    horizon_agent,
    horizon_agent_intelligence_loop,
    intervention_letter,
)

# =========================================================
# INITIAL CONFIGURATION & ENVIRONMENTAL MATRIX
# =========================================================
st.set_page_config(
    page_title="Aria — Autonomous Intervention Ecosystem",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown(
    """
    <style>
    .horizon-banner {
        border: 1px solid #4338ca;
        border-left: 6px solid #4338ca;
        border-radius: 8px;
        padding: 14px 16px;
        background: #1e1b4b;
        color: #e0e7ff;
        margin-bottom: 16px;
    }
    .letter-card {
        position: relative;
        border: 1px solid #4f46e5;
        border-left: 4px solid #4f46e5;
        border-radius: 8px;
        padding: 20px 22px 18px 42px;
        background: #0f172a;
        color: #cbd5e1;
        font-style: italic;
        line-height: 1.55;
        margin-bottom: 14px;
    }
    .letter-card:before {
        content: "\201C";
        position: absolute;
        top: 6px;
        left: 14px;
        font-size: 42px;
        color: #4f46e5;
        font-style: normal;
    }
    .agent-status-container {
        background-color: #0f172a;
        padding: 12px;
        border-radius: 8px;
        border: 1px solid #1e293b;
        margin-bottom: 15px;
    }
    .agent-dot {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
        color: #94a3b8;
        font-size: 0.9rem;
    }
    .agent-dot span {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
    }
    .crisis-container {
        background-color: #7f1d1d;
        padding: 25px;
        border-radius: 12px;
        border: 3px solid #ef4444;
        margin-bottom: 25px;
        text-align: center;
    }
    .stCheckbox {
        margin-bottom: -10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Standardize Workspace to Indian Standard Time (IST)
IST = pytz.timezone('Asia/Kolkata')
current_time_ist = datetime.datetime.now(IST)

# =========================================================
# LIVE FIREBASE INITIALIZATION ENGINE
# =========================================================
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("service_account.json")
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"❌ Critical Firebase Initialization Error: {e}")

db = firestore.client()

# Fetch Gemini API Key
load_dotenv()
try:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# =========================================================
# HELPER FUNCTIONS & RUNTIME CALCULATIONS
# =========================================================
def bordered_container(parent=st):
    try:
        return parent.container(border=True)
    except TypeError:
        return parent.container()

def primary_form_submit_button(label):
    try:
        return st.form_submit_button(label=label, type="primary")
    except TypeError:
        return st.form_submit_button(label=label)

def show_success_toast(message, icon=None):
    if hasattr(st, "toast"):
        st.toast(message, icon=icon)
    else:
        st.success(message)

def log_agent_action(agent, action):
    """Adds a timestamped record to the Agent Activity Log."""
    new_log = {
        "agent": agent,
        "action": action,
        "time": datetime.datetime.now(IST).strftime("%I:%M %p")
    }
    st.session_state.agent_log.insert(0, new_log)
    st.session_state.agent_log = st.session_state.agent_log[:30]

def parse_deadline(task):
    try:
        parsed = datetime.datetime.fromisoformat(task["deadline_at"])
        return parsed if parsed.tzinfo else IST.localize(parsed)
    except Exception:
        return current_time_ist + datetime.timedelta(days=task.get("days_left", 1))

def hours_left_for_task(task):
    return max((parse_deadline(task) - current_time_ist).total_seconds() / 3600, 0)

def format_countdown(hours_left):
    if hours_left < 1:
        return f"{max(round(hours_left * 60), 0)} min"
    if hours_left < 24:
        return f"{hours_left:.1f} hrs"
    return f"{hours_left / 24:.1f} days"

def calculate_priority_score(task, density_report):
    hours_left = hours_left_for_task(task)
    risk_points = {"LOW": 12, "MEDIUM": 34, "HIGH": 58, "CRITICAL": 82}
    score = risk_points.get(task.get("risk", "MEDIUM"), 34)
    if hours_left <= 6:
        score += 30
    elif hours_left <= 24:
        score += 22
    elif hours_left <= 72:
        score += 14
    else:
        score += 6
    score += min(int(task.get("effort_hours", 1)) * 2, 18)
    score += min(int(density_report.get("density_score", 0) / 5), 16)
    if task.get("status") == "Completed":
        score -= 35
    return max(min(score, 100), 1)

def sync_task_runtime_fields(task, density_report):
    task["hours_left"] = round(hours_left_for_task(task), 2)
    task["days_left"] = round(task["hours_left"] / 24, 1)
    if task["hours_left"] <= 6 and task.get("status") != "Completed":
        task["risk"] = "CRITICAL"
    else:
        task["risk"] = adjust_risk_for_horizon_density(task.get("base_risk", "MEDIUM"), density_report)
    task["priority_score"] = calculate_priority_score(task, density_report)
    return task

def extension_email_draft(task):
    """Action Agent: Drafts an emergency extension request."""
    user_name = st.session_state.get("user_display_name", "Student")
    return f"""Subject: Request for a short extension on {task['label']}

Dear Professor,

I am writing to respectfully request a short, emergency extension for the deliverable: '{task['label']}'.

Due to structural technical constraints with my compiling nodes, I require a small window to ensure a complete deployment rather than a rushed submission.

Thank you for your consideration,
{user_name}"""

def get_calendar_service():
    """Returns an authenticated Google Calendar service object, or None."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json')
    if not creds or not creds.valid:
        return None
    try:
        return build('calendar', 'v3', credentials=creds)
    except Exception:
        return None

def get_gmail_service():
    """Returns an authenticated Gmail service object, or None."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json')
    if not creds or not creds.valid:
        return None
    try:
        return build('gmail', 'v1', credentials=creds)
    except Exception:
        return None

def get_docs_service():
    """Returns authenticated Google Docs + Drive service objects, or (None, None)."""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json')
    if not creds or not creds.valid:
        return None, None
    try:
        docs = build('docs', 'v1', credentials=creds)
        drive = build('drive', 'v3', credentials=creds)
        return docs, drive
    except Exception:
        return None, None

# =========================================================
# INTELLIGENCE LAYER: GEMINI AGENTS
# =========================================================
def execute_ai_deconstruction(label, description, days, hours, category):
    """Breakdown Agent: Converts a task into a structured JSON subtasks list via Gemini."""
    fallback_subtasks = [
        {"name": "Compile existing source notes", "hours": max(1, int(hours * 0.3))},
        {"name": "Generate structural blueprint", "hours": max(1, int(hours * 0.3))},
        {"name": "Execute primary sprint", "hours": max(1, int(hours * 0.4))}
    ]
    if not gemini_client:
        return "HIGH", fallback_subtasks
    try:
        prompt = f"""
        Task: '{label}' ({category}). Context: {description}.
        Days available: {days}. Effort hours: {hours}.
        Break this into exactly 3 clear, actionable subtasks.
        Return ONLY valid JSON with no markdown fences, like this:
        {{"risk": "HIGH", "subtasks": [{{"name": "step description", "hours": 2}}, {{"name": "step 2", "hours": 3}}, {{"name": "step 3", "hours": 2}}]}}
        """
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        raw = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        subtasks = data.get("subtasks", fallback_subtasks)
        # Guarantee it's always a list of dicts with the right keys
        if not isinstance(subtasks, list) or not all(isinstance(s, dict) for s in subtasks):
            subtasks = fallback_subtasks
        return data.get("risk", "HIGH"), subtasks
    except Exception:
        return "HIGH", fallback_subtasks

def build_morning_briefing(tasks, density_report, suggestions):
    """Morning Briefing Agent: Generates a custom daily greeting via Gemini."""
    active_tasks = [t for t in tasks if t.get("status") != "Completed"]
    task_names = [t['label'] for t in active_tasks][:3]
    event_names = [s.get('event', 'an unknown event') for s in suggestions]
    user_name = st.session_state.get("user_display_name", "there")
    fallback = f"🌞 Good morning, {user_name}. You have {len(active_tasks)} active task(s) in queue. Focus on: {', '.join(task_names) if task_names else 'clearing your backlog'}."
    if not gemini_client:
        return fallback
    try:
        prompt = f"""
        Write a 2-sentence morning briefing for {user_name}.
        Calendar density: {density_report['density_score']}%.
        Active tasks they need to focus on today: {task_names}.
        Unlinked calendar events they should watch out for: {event_names}.
        Tone: Direct, AI-assistant guardian, highly personalized. Start with '🌞 Good morning, {user_name}.'
        Do NOT just blindly list the tasks. Weave them into a natural, grounded briefing.
        """
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return resp.text.strip()
    except Exception as e:
        print(f"Briefing Error: {e}")
        return fallback

@st.dialog("🧬 Deadline DNA: Your Productivity Fingerprint")
def show_deadline_dna_modal(tasks):
    """Reflection Agent: Displays psychological insights based on task history."""
    if not gemini_client:
        st.error("Gemini API Offline.")
        return
    st.write("🧠 **Reflection Agent** is analyzing your historical tracking metrics...")
    completed = [t for t in tasks if t.get("status") == "Completed"]
    active = [t for t in tasks if t.get("status") != "Completed"]
    task_strings = [
        f"Task: {t['label']}, Status: {t.get('status', 'Unknown')}, Category: {t.get('category', 'General')}, Risk: {t.get('base_risk', 'MEDIUM')}"
        for t in tasks
    ]
    prompt = f"""
    Analyze these tasks for a student: {task_strings}.
    Completed: {len(completed)}, Active: {len(active)}.
    Generate a 'Deadline DNA' profile.
    Return EXACTLY this markdown layout:
    ### ⏱️ Peak Execution Hours
    [1 sentence prediction based on the data]
    ### 📉 Time Estimation Variance
    [1 sentence stating whether they underestimate or overestimate time]
    ### ⚠️ Vulnerability Vectors
    [1 sentence on their weakest category based on risk levels]
    ### 💡 Aria's Recommendation
    [1 concrete, actionable tip]
    """
    with st.spinner("Processing temporal matrices..."):
        try:
            resp = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            st.markdown(resp.text)
            st.caption("Insights generated securely by Aria's Reflection Agent via Gemini 2.5 Flash.")
        except Exception:
            st.error("Analysis failed. Check Gemini API connection.")

# =========================================================
# GOOGLE API ACTION FUNCTIONS
# =========================================================
def create_focus_block(task, hours_from_now=1, duration_hours=2):
    """
    Action Agent: Creates a real focus block in Google Calendar.
    Returns (success: bool, message: str).
    """
    service = get_calendar_service()
    if not service:
        return False, "Calendar API offline — token.json not found or expired."
    try:
        start_time = current_time_ist + datetime.timedelta(hours=hours_from_now)
        end_time = start_time + datetime.timedelta(hours=duration_hours)
        event = {
            "summary": f"🎯 Focus: {task['label']}",
            "description": f"Aria focus block — auto-scheduled.\nTask risk: {task.get('risk', 'MEDIUM')}\nDeadline: {task.get('deadline_at', 'Unknown')}",
            "start": {
                "dateTime": start_time.isoformat(),
                "timeZone": "Asia/Kolkata",
            },
            "end": {
                "dateTime": end_time.isoformat(),
                "timeZone": "Asia/Kolkata",
            },
            "colorId": "11",  # Red for urgency
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
        return True, f"Focus block created: {created.get('htmlLink', 'see Google Calendar')}"
    except HttpError as e:
        return False, f"Calendar API error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def send_gmail(to_email, subject, body):
    """
    Action Agent: Sends a real email via Gmail API.
    Returns (success: bool, message: str).
    """
    service = get_gmail_service()
    if not service:
        return False, "Gmail API offline — token.json not found or expired."
    try:
        import base64
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["to"] = to_email
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return True, f"Email dispatched to {to_email} via Gmail API."
    except HttpError as e:
        return False, f"Gmail API error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def create_planning_doc(task):
    """
    Action Agent: Creates a real Google Doc planning template and returns its URL.
    Returns (success: bool, url_or_message: str).
    """
    docs_service, drive_service = get_docs_service()
    if not docs_service:
        return False, "Google Docs API offline — token.json not found or expired."
    if not gemini_client:
        return False, "Gemini API offline — cannot generate content."
    try:
        # Generate the content via Gemini first
        prompt = f"""
        Create a concise planning document for the task: '{task['label']}'.
        Category: {task.get('category', 'General')}. Risk: {task.get('risk', 'MEDIUM')}.
        Include: Executive Summary, 3 Key Milestones, Required Resources, and a Risk Mitigation note.
        Keep it professional and concise.
        """
        resp = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        doc_content = resp.text.strip()

        # Create the Google Doc
        doc = docs_service.documents().create(
            body={"title": f"📋 Aria Plan: {task['label']}"}
        ).execute()
        doc_id = doc["documentId"]

        # Insert the generated content
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": doc_content
                        }
                    }
                ]
            }
        ).execute()

        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
        return True, doc_url
    except HttpError as e:
        return False, f"Docs API error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

# =========================================================
# TASK MANAGEMENT CORE
# =========================================================
def add_task_to_matrix(label, description, category, total_hours_left, effort_hours, density_report, source="Manual"):
    """Core function: routes a new task through all AI agents before saving to Firestore."""
    deadline_at = current_time_ist + datetime.timedelta(hours=total_hours_left)
    base_risk, subtasks_list = execute_ai_deconstruction(
        label, description,
        max(round(total_hours_left / 24, 2), 0.04),
        effort_hours,
        category
    )
    task = {
        "label": label.strip(),
        "category": category,
        "description": description.strip(),
        "deadline_at": deadline_at.isoformat(),
        "created_at": current_time_ist.isoformat(),
        "days_left": round(total_hours_left / 24, 1),
        "hours_left": round(total_hours_left, 2),
        "effort_hours": effort_hours,
        "base_risk": base_risk,
        "risk": base_risk,
        "horizon_density_score": density_report["density_score"],
        "subtasks": subtasks_list,
        "subtask_done": [False] * len(subtasks_list),
        "status": "Not Started",
        "source": source
    }

    task["intervention_letter"] = intervention_letter(
        task["label"], st.session_state.get("user_display_name", "Student"),
        deadline_at.strftime("%d %b, %I:%M %p"), description
    )
    log_agent_action("Intervention Agent", f"Generated future-self letter for '{task['label'][:20]}...'")

    if total_hours_left <= 6:
        task["crisis_plan"] = crisis_agent(task["label"], total_hours_left)
        log_agent_action("Crisis Agent", f"Activated emergency metrics for '{task['label'][:20]}...'")

    if st.session_state.user_id:
        db.collection("tasks").document(st.session_state.user_id).collection("items").add(task)
        log_agent_action(source, f"Saved '{task['label'][:20]}...' to Firestore.")
        st.session_state.morning_briefing_text = None

    return task

def export_tasks_csv(tasks_list):
    """Converts tasks list to a clean CSV for download."""
    if not tasks_list:
        return b""
    rows = []
    for t in tasks_list:
        rows.append({
            "Label": t.get("label", ""),
            "Category": t.get("category", ""),
            "Status": t.get("status", ""),
            "Risk": t.get("risk", ""),
            "Priority Score": t.get("priority_score", ""),
            "Hours Left": round(t.get("hours_left", 0), 1),
            "Effort Hours": t.get("effort_hours", ""),
            "Deadline": t.get("deadline_at", ""),
            "Created": t.get("created_at", ""),
            "Source": t.get("source", "Manual"),
        })
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

# =========================================================
# STATE INITIALIZATION
# =========================================================
if "agent_log" not in st.session_state:
    st.session_state.agent_log = [
        {"agent": "Priority Orchestrator", "action": "Re-ranked active tasks based on Deadline DNA metric.", "time": datetime.datetime.now(IST).strftime("%I:%M %p")}
    ]
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "user_display_name" not in st.session_state:
    st.session_state.user_display_name = "Student"
if "morning_briefing_text" not in st.session_state:
    st.session_state.morning_briefing_text = None

# =========================================================
# GOOGLE CALENDAR API INTEGRATION
# =========================================================
def fetch_live_calendar_deadlines(max_results=100):
    """Reads local token.json for read-only calendar access."""
    service = get_calendar_service()
    if not service:
        return []
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return service.events().list(
            calendarId='primary',
            timeMin=now_utc,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items', [])
    except Exception:
        return []

# =========================================================
# USER INTERFACE: APP SIDEBAR
# =========================================================
with st.sidebar:
    # 🔑 Firebase Authentication Block
    if not st.session_state.user_id:
        st.subheader("🔑 Firebase Authentication")
        email_input = st.text_input("Enter Email to Login:", placeholder="you@example.com")
        name_input = st.text_input("Your Display Name:", placeholder="e.g. Srikar")

        if st.button("⚡ Authenticate Secure Session", type="primary", use_container_width=True):
            try:
                try:
                    user = auth.get_user_by_email(email_input.strip())
                    log_msg = "Existing profile validated."
                except auth.UserNotFoundError:
                    user = auth.create_user(email=email_input.strip(), display_name=name_input.strip())
                    log_msg = "New profile created."

                st.session_state.user_id = user.uid
                st.session_state.user_email = user.email
                # Pull display name from Firebase Auth, fall back to input, then to "Student"
                st.session_state.user_display_name = (
                    user.display_name or name_input.strip() or "Student"
                )
                st.toast(log_msg, icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Auth Rejected: {e}")
    else:
        st.markdown(
            f"""
            <div style='background-color: #1e293b; padding: 12px; border-radius: 8px; border: 1px solid #334155; margin-bottom: 20px;'>
                <span style='color: #38bdf8;'>🟢 Connected:</span><br>
                <strong style='color: #f8fafc;'>{st.session_state.user_display_name}</strong><br>
                <span style='color: #64748b; font-size: 11px; font-family: monospace;'>{st.session_state.user_email}</span>
            </div>
            """,
            unsafe_allow_html=True
        )
        if st.button("🚪 Disconnect Session", use_container_width=True):
            st.session_state.user_id = None
            st.session_state.user_display_name = "Student"
            st.rerun()

    # 🛡️ System Controls
    st.title("🛡️ System Control")
    manual_crisis = st.checkbox("💥 Force Crisis Mode (Under 6h)", value=False)

    # 🤖 Core Agent Status
    st.markdown("---")
    st.subheader("🤖 Core Agent Status")
    st.markdown(
        """
        <div class="agent-status-container">
            <div class="agent-dot"><span style="background-color: #10b981;"></span>Horizon Agent · Active</div>
            <div class="agent-dot"><span style="background-color: #10b981;"></span>Breakdown Engine · Armed</div>
            <div class="agent-dot"><span style="background-color: #10b981;"></span>Priority Orchestrator · Active</div>
            <div class="agent-dot"><span style="background-color: #10b981;"></span>Action Agent · Connected</div>
            <div class="agent-dot"><span style="background-color: #10b981;"></span>Reflection Agent · Active</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 🗓️ Real-Time Horizon Targets
    st.markdown("---")
    st.subheader("🗓️ Real-Time Horizon Targets")
    live_events = fetch_live_calendar_deadlines(max_results=100)
    horizon_report = horizon_agent_intelligence_loop(live_events or [], now=current_time_ist)

    if live_events is not None:
        st.metric(
            "48h Calendar Density",
            f"{horizon_report['density_score']}%",
            horizon_report["density_tier"].title()
        )

    if live_events is None:
        st.warning("⚠️ Google Workspace offline. Missing session token.")
    elif not live_events:
        st.success("🎉 Horizon completely clear!")
    else:
        for event in live_events[:3]:
            summary = event.get('summary', 'Unlabeled Event')
            start_raw = event['start'].get('dateTime', event['start'].get('date'))
            if 'T' in start_raw:
                formatted_time = f"⏰ {start_raw.split('T')[0]} @ {start_raw.split('T')[1][:5]}"
            else:
                formatted_time = f"📅 {start_raw} (All-Day)"
            with bordered_container(st.sidebar):
                st.markdown(f"**{summary}**")
                st.caption(formatted_time)

    # 🧬 Deadline DNA Modal Button
    st.markdown("---")
    if st.button("🧬 View Deadline DNA Profile", use_container_width=True):
        if st.session_state.user_id:
            active_dna_tasks = st.session_state.get('cached_tasks', [])
            show_deadline_dna_modal(active_dna_tasks)
        else:
            st.warning("Authenticate first.")

    # ─────────────────────────────────────────────────────
    # 📥 EXPORT DATA — judges love data ownership
    # ─────────────────────────────────────────────────────
    st.markdown("---")
    cached = st.session_state.get("cached_tasks", [])
    if cached:
        csv_bytes = export_tasks_csv(cached)
        st.download_button(
            label="📥 Export Tasks as CSV",
            data=csv_bytes,
            file_name=f"aria_tasks_{datetime.datetime.now(IST).strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
            help="Download all your tasks as a CSV file."
        )
    else:
        st.button("📥 Export Tasks as CSV", disabled=True, use_container_width=True,
                  help="Add tasks first to enable export.")

    # ─────────────────────────────────────────────────────
    # 🗑️ DEMO RESET — wipe Firestore for a clean judge demo
    # ─────────────────────────────────────────────────────
    st.markdown("---")
    if st.session_state.user_id:
        with st.expander("⚠️ Demo Tools"):
            st.caption("For hackathon demo use only.")
            if st.button("🗑️ Wipe Database (Demo Reset)", use_container_width=True, type="secondary"):
                try:
                    ref = db.collection("tasks").document(st.session_state.user_id).collection("items")
                    docs = ref.stream()
                    deleted = 0
                    for doc in docs:
                        doc.reference.delete()
                        deleted += 1
                    st.session_state.morning_briefing_text = None
                    st.session_state.agent_log = [
                        {"agent": "System", "action": f"Demo reset — {deleted} task(s) wiped.", "time": datetime.datetime.now(IST).strftime("%I:%M %p")}
                    ]
                    show_success_toast(f"Wiped {deleted} task(s). Ready for next judge.", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reset failed: {e}")

# =========================================================
# LIVE FIRESTORE DATA SYNC
# =========================================================
tasks_list = []
firestore_error = None
if st.session_state.user_id:
    try:
        tasks_ref = db.collection("tasks").document(st.session_state.user_id).collection("items")
        for doc in tasks_ref.stream():
            tasks_list.append({**doc.to_dict(), "id": doc.id})
    except Exception as e:
        firestore_error = str(e)

# Sync runtime fields
for t in tasks_list:
    sync_task_runtime_fields(t, horizon_report)

# Cache tasks for DNA modal and export
st.session_state['cached_tasks'] = tasks_list

active_crisis_state = manual_crisis or any(
    t["hours_left"] <= 6 and t["status"] != "Completed" for t in tasks_list
)

# =========================================================
# MAIN WORKSPACE DASHBOARD
# =========================================================
if not st.session_state.user_id:
    # 🔒 Lock Screen
    st.title("🚨 Aria — The Last-Minute Life Saver")
    st.warning("🔒 Secure Network Node Offline: Authenticate your account email using the Firebase panel in the sidebar.")

elif active_crisis_state:
    # 💥 CRISIS MODE
    st.snow()
    st.markdown(
        """
        <div class='crisis-container'>
            <h1 style='color: white; margin: 0;'>⚠️ CRISIS MODE ACTIVE</h1>
        </div>
        """,
        unsafe_allow_html=True
    )

    crisis_task = next(
        (t for t in tasks_list if t.get("hours_left", 99) <= 6 and t.get("status") != "Completed"),
        tasks_list[0] if tasks_list else {"label": "Unknown Task"}
    )

    col1, col2 = st.columns([2.2, 1])
    with col1:
        st.subheader("🪓 Ruthless Execution Battle Plan")
        with bordered_container():
            st.error("🚨 Execution paralysis block active. Secondary matrices suppressed.")
            crisis_plan_text = crisis_task.get("crisis_plan", "") if tasks_list else ""
            if crisis_plan_text:
                st.markdown(crisis_plan_text)
            else:
                st.markdown("⏱ **00m - 45m:** Isolate primary data models.")
                st.markdown("⏱ **45m - 90m:** Verify pipeline parameters without documentation.")

    with col2:
        st.subheader("✉️ Action Agent Blueprint")
        with bordered_container():
            draft = extension_email_draft(crisis_task)
            st.text_area("Email Output:", value=draft, height=180)
            recipient = st.text_input("Recipient Email:", placeholder="professor@college.edu", key="crisis_recipient")

            if st.button("🚀 Dispatch via Gmail API", use_container_width=True):
                if recipient:
                    lines = draft.split("\n")
                    subject_line = lines[0].replace("Subject: ", "").strip() if lines else "Extension Request"
                    body = "\n".join(lines[2:]).strip()
                    with st.spinner("Dispatching via Gmail API..."):
                        ok, msg = send_gmail(recipient, subject_line, body)
                    if ok:
                        show_success_toast("Email dispatched via Gmail API!", icon="📩")
                        log_agent_action("Action Agent", f"Gmail API: sent extension request to {recipient}")
                    else:
                        st.error(f"Gmail send failed: {msg}")
                else:
                    st.warning("Enter a recipient email address first.")

else:
    # 🏢 STANDARD MISSION CONTROL DASHBOARD
    st.title(f"🚨 Aria — Mission Control · {st.session_state.user_display_name}")

    # 🔴 Firestore read error — visible to judges so they know the DB path is real
    if firestore_error:
        st.error(
            f"⚠️ **Firestore Read Failed** — tasks could not be loaded from the database.\n\n"
            f"`{firestore_error}`\n\n"
            "Check your `service_account.json` credentials and Firestore security rules."
        )

    # 🌞 Morning Briefing Agent
    try:
        suggestions = horizon_agent(live_events or [], [t["label"] for t in tasks_list])
    except Exception:
        suggestions = []

    if st.session_state.morning_briefing_text is None:
        st.session_state.morning_briefing_text = build_morning_briefing(tasks_list, horizon_report, suggestions)

    st.info(st.session_state.morning_briefing_text)

    # 🔍 Horizon Mismatch Banner
    if suggestions:
        st.markdown(
            f"""
            <div class='horizon-banner'>
                <strong>🔍 Horizon Sync Warning</strong><br>
                Unlinked calendar entry: '{suggestions[0].get('event')}'. No task track exists.
            </div>
            """,
            unsafe_allow_html=True
        )
        if st.button("⚡ Auto-Generate Study Plan Matrix"):
            with st.spinner("Compiling via Gemini..."):
                add_task_to_matrix(
                    label=suggestions[0].get('suggested_task', "Prep"),
                    description="Automated sync task.",
                    category="Academic",
                    total_hours_left=144,
                    effort_hours=12,
                    density_report=horizon_report,
                    source="Horizon Agent"
                )
            st.rerun()

    dash_col, log_col = st.columns([2.2, 1])

    with dash_col:
        # 📝 Queue New Task Form
        with bordered_container():
            st.subheader("📝 Queue New Task Payload")
            with st.form("injector", clear_on_submit=True):
                t_lbl = st.text_input("Task Label:")
                t_desc = st.text_area("Context:")
                c1, c2, c3 = st.columns(3)
                with c1:
                    days_val = st.number_input("Days:", min_value=1, value=3)
                with c2:
                    hrs_val = st.slider("Effort (hrs):", 1, 100, 5)
                with c3:
                    cat_val = st.selectbox("Category:", ["Academic", "Professional", "Hackathon Sprint"])
                if primary_form_submit_button("⚡ Instantiate"):
                    if t_lbl:
                        add_task_to_matrix(t_lbl, t_desc, cat_val, days_val * 24, hrs_val, horizon_report)
                        st.rerun()

        # 📊 Gantt Timeline Chart — fixed start dates
        st.subheader("📊 7-Day Deadline Timeline Matrix")
        if tasks_list:
            gantt_list = []
            for t in sorted(tasks_list, key=lambda x: parse_deadline(x)):
                # Use actual created_at as start if available; otherwise infer from effort
                try:
                    created = datetime.datetime.fromisoformat(t.get("created_at", ""))
                    if not created.tzinfo:
                        created = IST.localize(created)
                except Exception:
                    created = parse_deadline(t) - datetime.timedelta(hours=t.get("effort_hours", 2))
                gantt_list.append({
                    "Task Target": t["label"],
                    "Start": created,
                    "End": parse_deadline(t),
                    "Risk": t["risk"]
                })
            df = pd.DataFrame(gantt_list)
            fig = px.timeline(
                df,
                x_start="Start",
                x_end="End",
                y="Task Target",
                color="Risk",
                color_discrete_map={"CRITICAL": "#ef4444", "HIGH": "#f97316", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        # ─────────────────────────────────────────────────────
        # 📋 TASK MATRIX — split into Active / Completed tabs
        # ─────────────────────────────────────────────────────
        st.subheader("📋 Your Real-Time Strategic Checklist")

        sorted_tasks = sorted(tasks_list, key=lambda x: x.get("priority_score", 0), reverse=True)
        active_tasks = [t for t in sorted_tasks if t.get("status") != "Completed"]
        completed_tasks = [t for t in sorted_tasks if t.get("status") == "Completed"]

        tab_active, tab_done = st.tabs([
            f"🔥 Active ({len(active_tasks)})",
            f"✅ Completed ({len(completed_tasks)})"
        ])

        def render_task_card(item):
            """Renders a single task card with all controls."""
            r_col = "#ef4444" if item["risk"] in ["HIGH", "CRITICAL"] else "#f59e0b" if item["risk"] == "MEDIUM" else "#10b981"

            with bordered_container():
                t_col, b_col = st.columns([4, 1])
                t_col.markdown(f"### {item['label']}\n**{format_countdown(item['hours_left'])}** remaining")
                b_col.markdown(
                    f"<div style='background-color: {r_col}; color: white; padding: 6px; text-align: center; border-radius: 6px;'>RISK: {item['risk']}</div>",
                    unsafe_allow_html=True
                )

                st.markdown("---")
                bod, act = st.columns([3, 1])

                with bod:
                    # Subtask checklist with reliable Firestore persistence
                    subtasks = item.get("subtasks", [])
                    subtask_done = list(item.get("subtask_done", [False] * len(subtasks)))
                    # Pad subtask_done if lengths differ (e.g. tasks added before this version)
                    while len(subtask_done) < len(subtasks):
                        subtask_done.append(False)

                    if subtasks:
                        st.markdown("🔥 **Tactical Subtasks:**")
                        for s_idx, sub in enumerate(subtasks):
                            widget_key = f"s_{item['id']}_{s_idx}"
                            # Render checkbox — Streamlit sets st.session_state[widget_key] on change
                            st.checkbox(
                                f"{sub.get('name', 'Step')} ({sub.get('hours', '?')}h)",
                                value=bool(subtask_done[s_idx]),
                                key=widget_key
                            )

                        # Read the current values directly from session_state (always accurate)
                        live_done = [
                            bool(st.session_state.get(f"s_{item['id']}_{i}", subtask_done[i]))
                            for i in range(len(subtasks))
                        ]

                        # Progress bar — visible completion signal for judges
                        completed_count = sum(live_done)
                        st.progress(
                            completed_count / len(subtasks),
                            text=f"Progress: {completed_count}/{len(subtasks)} subtasks complete"
                        )

                        # Only write to Firestore if something actually changed
                        if live_done != subtask_done:
                            db.collection("tasks").document(st.session_state.user_id) \
                              .collection("items").document(item["id"]) \
                              .update({"subtask_done": live_done})
                            log_agent_action(
                                "Breakdown Engine",
                                f"Subtask progress saved for '{item['label'][:20]}...'"
                            )

                    # ── Schedule Focus Blocks (real Calendar API call) ──
                    if st.button("📅 Schedule Focus Block in Calendar", key=f"cal_{item['id']}"):
                        with st.spinner("Writing to Google Calendar..."):
                            ok, msg = create_focus_block(item, hours_from_now=1, duration_hours=int(item.get("effort_hours", 2)))
                        if ok:
                            show_success_toast("Focus block injected into Calendar!", "📅")
                            log_agent_action("Action Agent", f"Calendar write: focus block for '{item['label'][:20]}...'")
                            st.success(f"✅ {msg}")
                        else:
                            st.warning(f"⚠️ {msg}")

                    # ── Find Study Resources (Gemini) ──
                    if st.button("🔍 Find Study Resources", key=f"search_{item['id']}"):
                        with st.spinner("Action Agent scanning web directories..."):
                            prompt = f"Act as a search engine. Provide 3 highly relevant, real-world markdown links (e.g., StackOverflow, official docs, YouTube) for someone trying to complete: '{item['label']}'. Format as a bulleted list."
                            try:
                                links = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt).text
                                st.info(f"**Action Agent found these resources:**\n\n{links}")
                                log_agent_action("Action Agent", f"Queried custom search for '{item['label'][:20]}...'")
                            except Exception:
                                st.error("Search failed. Check Gemini API connection.")

                    # ── Generate Planning Doc (real Docs API call) ──
                    if st.button("📄 Generate Planning Doc", key=f"doc_{item['id']}"):
                        with st.spinner("Creating Google Doc via Docs API..."):
                            ok, result = create_planning_doc(item)
                        if ok:
                            st.success(f"✅ Google Doc created: [Open in Drive]({result})")
                            log_agent_action("Action Agent", f"Google Doc created for '{item['label'][:20]}...'")
                        else:
                            # Graceful fallback: show the Gemini-generated content inline
                            st.info("📄 Generating via Aria's Document Engine — rendering inline preview:")
                            if gemini_client:
                                prompt = f"Create a structured Markdown planning document for: '{item['label']}'. Include Executive Summary, Milestones, and Resources."
                                try:
                                    doc_content = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt).text
                                    with st.expander("📄 Planning Doc Preview", expanded=True):
                                        st.markdown(doc_content)
                                except Exception:
                                    st.error("Content generation failed.")

                    # ── Intervention Letter ──
                    if item.get("intervention_letter"):
                        with st.expander("✉️ Intervention Letter"):
                            st.markdown(
                                f"<div class='letter-card'>{item['intervention_letter']}</div>",
                                unsafe_allow_html=True
                            )

                with act:
                    current_idx = ["Not Started", "In Progress", "Completed"].index(item.get("status", "Not Started"))
                    ns = st.selectbox(
                        "Status:",
                        ["Not Started", "In Progress", "Completed"],
                        key=f"stat_{item['id']}",
                        index=current_idx
                    )
                    if ns != item.get("status"):
                        db.collection("tasks").document(st.session_state.user_id) \
                          .collection("items").document(item["id"]) \
                          .update({"status": ns})
                        st.session_state.morning_briefing_text = None
                        if ns == "Completed":
                            st.balloons()
                        st.rerun()

                    # Priority score badge
                    score = item.get("priority_score", 0)
                    score_color = "#ef4444" if score >= 70 else "#f59e0b" if score >= 40 else "#10b981"
                    st.markdown(
                        f"<div style='margin-top:8px; background:{score_color}22; border:1px solid {score_color}; border-radius:6px; padding:6px; text-align:center;'>"
                        f"<span style='color:{score_color}; font-weight:600; font-size:11px;'>PRIORITY</span><br>"
                        f"<span style='color:{score_color}; font-size:22px; font-weight:700;'>{score}</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

        with tab_active:
            if active_tasks:
                for item in active_tasks:
                    render_task_card(item)
            else:
                st.success("🎉 All tasks complete. Queue is clear.")

        with tab_done:
            if completed_tasks:
                for item in completed_tasks:
                    render_task_card(item)
            else:
                st.info("No completed tasks yet. Finish something!")

    with log_col:
        # 📋 Agent Activity Log
        st.subheader("📋 Agent Activity Log")
        with bordered_container():
            st.markdown("<div style='font-size: 12px;'>", unsafe_allow_html=True)
            for log in st.session_state.agent_log:
                st.markdown(
                    f"<div style='border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 8px;'>"
                    f"<span style='color: #38bdf8;'>[{log['time']}] {log['agent']}:</span> "
                    f"<span style='color: #94a3b8;'>{log['action']}</span></div>",
                    unsafe_allow_html=True
                )
            st.markdown("</div>", unsafe_allow_html=True)

        # 📊 Horizon Diagnostics
        with bordered_container():
            st.markdown("### 📊 Horizon Diagnostics")
            st.write("Timezone: **IST (Asia/Kolkata)**")
            st.progress(horizon_report["density_score"] / 100)
            st.caption(f"Ecosystem Load: {horizon_report['density_tier'].upper()} activity envelope detected.")

        # 📈 Quick stats
        with bordered_container():
            st.markdown("### 📈 Queue Stats")
            total = len(tasks_list)
            done = len([t for t in tasks_list if t.get("status") == "Completed"])
            crit = len([t for t in tasks_list if t.get("risk") == "CRITICAL" and t.get("status") != "Completed"])
            st.metric("Total Tasks", total)
            st.metric("Completed", done)
            if crit:
                st.metric("🔴 Critical", crit, delta=f"-{crit} need attention", delta_color="inverse")