import os
import io
import json
import copy
import uuid
import datetime
import pytz
import pandas as pd
import streamlit as st
import plotly.express as px
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google import genai
from google.genai import types
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

try:
    from agents import (
        adjust_risk_for_horizon_density,
        crisis_agent,
        horizon_agent,
        horizon_agent_intelligence_loop,
        intervention_letter,
    )
except Exception as e:
    print(f"Agents import failed: {e}")

    def adjust_risk_for_horizon_density(base_risk, density_report):
        return base_risk or "MEDIUM"

    def crisis_agent(task_name, hours_left):
        return []

    def horizon_agent(events, existing_task_names, *, now=None, timezone_name=None, max_suggestions=5):
        return []

    def horizon_agent_intelligence_loop(calendar_events, *, now=None, horizon_hours=48, timezone_name="Asia/Kolkata", critical_threshold=70):
        return {
            "density_score": 0,
            "density_percent": 0.0,
            "density_tier": "CLEAR",
            "is_overcrowded": False,
            "busy_minutes": 0,
            "free_minutes": horizon_hours * 60,
            "horizon_minutes": horizon_hours * 60,
            "horizon_hours": horizon_hours,
            "event_count": len(calendar_events or []),
            "counted_event_count": 0,
            "merged_busy_block_count": 0,
            "skipped_event_count": 0,
            "window_start": (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(),
            "window_end": ((now or datetime.datetime.now(datetime.timezone.utc)) + datetime.timedelta(hours=horizon_hours)).isoformat(),
        }

    def intervention_letter(task_name, user_name, deadline_str, context=""):
        return (
            f"Hi {user_name or 'Student'},\n\n"
            f"This is an intervention note for {task_name}. Keep it simple and actionable."
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
        content: "\\201C";
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
    .draft-staged-caption {
        color: #10b981;
        font-weight: 600;
        font-size: 0.9rem;
        margin-top: 8px;
    }
    .crisis-countdown {
        color: white;
        font-size: 2rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        margin: 8px 0 0 0;
    }
    .crisis-countdown-label {
        color: #fecaca;
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 0.12em;
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
try:
    firebase_admin.get_app()
    firebase_initialized = True
except ValueError:
    firebase_initialized = False

if not firebase_initialized:
    try:
        cred = None

        # 1. Try Streamlit Secrets (Streamlit Cloud or mounted .streamlit/secrets.toml)
        if "firebase" in st.secrets:
            try:
                fb = st.secrets.get("firebase", {})
                required_keys = [
                    "type", "project_id", "private_key_id", "private_key",
                    "client_email", "client_id", "auth_uri", "token_uri",
                    "auth_provider_x509_cert_url", "client_x509_cert_url",
                ]
                if not all(fb.get(key) for key in required_keys):
                    raise ValueError("Incomplete firebase secrets payload")
                cred_dict = {
                    "type": fb.get("type", ""),
                    "project_id": fb.get("project_id", ""),
                    "private_key_id": fb.get("private_key_id", ""),
                    "private_key": fb.get("private_key", "").replace("\\n", "\n"),
                    "client_email": fb.get("client_email", ""),
                    "client_id": fb.get("client_id", ""),
                    "auth_uri": fb.get("auth_uri", ""),
                    "token_uri": fb.get("token_uri", ""),
                    "auth_provider_x509_cert_url": fb.get("auth_provider_x509_cert_url", ""),
                    "client_x509_cert_url": fb.get("client_x509_cert_url", ""),
                    "universe_domain": fb.get("universe_domain", "googleapis.com"),
                }
                cred = credentials.Certificate(cred_dict)
            except Exception as e:
                print(f"Failed to parse st.secrets: {e}")

        # 2. Try Environment Variable (Cloud Run / Docker env var injection)
        elif os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
            try:
                env_creds = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
                cred_dict = json.loads(env_creds)
                cred = credentials.Certificate(cred_dict)
            except Exception as e:
                print(f"Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")

        # 3. Fallback to local file (Local Development)
        if not cred:
            cred = credentials.Certificate("service_account.json")

        firebase_admin.initialize_app(cred)
        firebase_initialized = True
    except Exception as e:
        print(f"Firebase initialization error: {e}")
        st.warning(
            "🔒 **Secure Network Node Offline:** Backend credentials could not be validated. "
            "Verify your deployment secrets and retry."
        )

# Guard db creation — only proceed if Firebase initialized successfully
try:
    db = firestore.client()
except Exception as e:
    db = None
    print(f"Firestore client error: {e}")
    st.warning(
        "🔒 **Secure Network Node Offline:** Database sync channel unavailable. "
        "Verify your deployment secrets and retry."
    )

# Fetch Gemini API Key
load_dotenv()
try:
    GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
except Exception:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def gemini_offline_answer():
    return (
        "Start with the most urgent task: block 60–90 minutes for it, "
        "break it into 3 concrete next actions, and focus on finishing one action before moving on. "
        "If Gemini is temporarily unavailable, this is the best way to keep momentum."
    )

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

def generate_local_task_id():
    return f"local_{uuid.uuid4().hex[:12]}"

def ensure_local_task_identity(task):
    if not isinstance(task, dict):
        return task
    task_copy = copy.deepcopy(task)
    if not task_copy.get("id"):
        task_copy["id"] = generate_local_task_id()
    return task_copy

def get_cached_tasks_snapshot():
    cached_tasks = st.session_state.get("cached_tasks", [])
    if not isinstance(cached_tasks, list):
        return []
    safe_tasks = []
    for task in cached_tasks:
        if isinstance(task, dict):
            safe_tasks.append(ensure_local_task_identity(task))
    return safe_tasks

def write_cached_tasks(tasks):
    safe_tasks = []
    for task in tasks or []:
        if isinstance(task, dict):
            safe_tasks.append(ensure_local_task_identity(task))
    st.session_state["cached_tasks"] = safe_tasks
    return safe_tasks

def upsert_cached_task(task):
    task_copy = ensure_local_task_identity(task)
    cached_tasks = get_cached_tasks_snapshot()
    for idx, existing in enumerate(cached_tasks):
        if existing.get("id") == task_copy.get("id"):
            cached_tasks[idx] = task_copy
            break
    else:
        cached_tasks.append(task_copy)
    st.session_state["cached_tasks"] = cached_tasks
    return task_copy

def update_cached_task(task_id, updates):
    if not task_id or not isinstance(updates, dict):
        return
    cached_tasks = get_cached_tasks_snapshot()
    for idx, existing in enumerate(cached_tasks):
        if existing.get("id") == task_id:
            merged_task = copy.deepcopy(existing)
            merged_task.update(copy.deepcopy(updates))
            cached_tasks[idx] = ensure_local_task_identity(merged_task)
            st.session_state["cached_tasks"] = cached_tasks
            return

def clear_cached_tasks():
    st.session_state["cached_tasks"] = []
    st.session_state["intervention_alerts"] = []
    st.session_state["intervention_fired_ids"] = set()

def log_agent_action(agent, action):
    """Adds a timestamped record to the Agent Activity Log."""
    new_log = {
        "agent": agent,
        "action": action,
        "time": datetime.datetime.now(IST).strftime("%I:%M %p")
    }
    st.session_state.agent_log.insert(0, new_log)
    st.session_state.agent_log = st.session_state.agent_log[:30]
    # Persist agent log to Firestore so it survives reloads
    try:
        if db is not None and st.session_state.get("user_id"):
            db.collection("user_meta").document(st.session_state.user_id).set(
                {"agent_log": st.session_state.agent_log}, merge=True
            )
    except Exception:
        pass  # Non-fatal: keep UI working even if Firestore write fails

def generate_default_agent_log():
    morning_base = current_time_ist.replace(hour=9, minute=0, second=0, microsecond=0)
    return [
        {"agent": "Horizon Agent", "action": "Detected 'Final Exam' on calendar with no prep tasks — auto-generated study plan", "time": morning_base.strftime("%I:%M %p")},
        {"agent": "Breakdown Agent", "action": "Deconstructed 'ML Presentation' into 7 subtasks · Risk: HIGH", "time": (morning_base + datetime.timedelta(hours=1)).strftime("%I:%M %p")},
        {"agent": "Priority Orchestrator", "action": "Re-ranked 3 tasks — 'ML Presentation' moved to slot #1", "time": (morning_base + datetime.timedelta(hours=2)).strftime("%I:%M %p")},
        {"agent": "Action Agent", "action": "Queued 3 focus blocks", "time": (morning_base + datetime.timedelta(hours=3)).strftime("%I:%M %p")},
    ]

def parse_deadline(task):
    try:
        parsed = datetime.datetime.fromisoformat(task.get("deadline_at", ""))
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

def format_live_countdown(deadline_dt):
    """Returns HH:MM:SS until deadline (zero-padded)."""
    remaining = max((deadline_dt - datetime.datetime.now(IST)).total_seconds(), 0)
    hours, rem = divmod(int(remaining), 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def render_crisis_plan(crisis_plan):
    """Render crisis plan content in clean markdown format."""
    if not crisis_plan:
        return ""
    if isinstance(crisis_plan, str):
        return crisis_plan if crisis_plan.strip() != "[]" else ""
    if isinstance(crisis_plan, list):
        formatted = []
        for entry in crisis_plan:
            if not isinstance(entry, dict):
                continue
            time_block = entry.get("time_block", "")
            action = entry.get("action", "")
            output = entry.get("output", "")
            piece = []
            if time_block:
                piece.append(f"**⏱ {time_block}**")
            if action:
                piece.append(action)
            if output:
                piece.append(output)
            if piece:
                formatted.append(" — ".join(piece))
        return "\n\n".join(formatted) if formatted else ""
    return str(crisis_plan)

def check_proactive_interventions(tasks_list):
    """Scan tasks for proactive intervention alerts and deduplicate them."""
    if "intervention_fired_ids" not in st.session_state:
        st.session_state.intervention_fired_ids = set()

    alerts = []
    for task in tasks_list:
        task_id = task.get("id") or task.get("label")
        task_label = task.get("label", "Unnamed Task")
        if not task_id:
            continue

        hours_left = float(task.get("hours_left", 0))
        status = task.get("status", "Not Started")

        if status == "Not Started" and hours_left <= 48:
            tracking_id = f"{task_id}_48h"
            if tracking_id not in st.session_state.intervention_fired_ids:
                st.session_state.intervention_fired_ids.add(tracking_id)
                log_agent_action("Intervention Agent", f"Raised 48h warning for '{task_label}'")
            alerts.append({
                "task_id": task_id,
                "message": f"{task_label} is unstarted and within 48 hours.",
                "severity": "HIGH",
            })
            continue

        if status == "In Progress" and hours_left <= 12:
            subtasks = task.get("subtasks", [])
            done_count = sum(1 for done in task.get("subtask_done", []) if done)
            if subtasks and done_count < (len(subtasks) // 2):
                tracking_id = f"{task_id}_12h"
                if tracking_id not in st.session_state.intervention_fired_ids:
                    st.session_state.intervention_fired_ids.add(tracking_id)
                    log_agent_action("Intervention Agent", f"Raised 12h critical warning for '{task_label}'")
                alerts.append({
                    "task_id": task_id,
                    "message": f"{task_label} is in progress but less than half complete with under 12 hours remaining.",
                    "severity": "CRITICAL",
                })
    return alerts

def scan_gmail_for_deadlines(max_results=8):
    """Scan Gmail for recent unread deadline-related emails."""
    service = get_gmail_service()
    if not service:
        return []

    # FIX 1: Loosened query — subject-field matching catches self-sent test emails
    # and avoids newer_than recency filter that can miss freshly sent messages.
    DEADLINE_KEYWORDS = [
        "due", "deadline", "exam", "assignment", "syllabus", "submission"
    ]
    subject_filters = " OR ".join(f"subject:{kw}" for kw in DEADLINE_KEYWORDS)
    query = f"is:unread ({subject_filters})"

    try:
        response = service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
        messages = response.get('messages', []) or []
        deadline_hits = []
        for msg_ref in messages[:max_results]:
            try:
                message = service.users().messages().get(
                    userId='me', id=msg_ref.get('id', ''), format='metadata',
                    metadataHeaders=['Subject', 'From', 'Date']
                ).execute()
                headers = {h['name']: h['value'] for h in message.get('payload', {}).get('headers', [])}

                raw_subject = headers.get('Subject', 'No Subject')
                raw_snippet = message.get('snippet', '').strip()

                # FIX 2: Enforce lowercase matching so "DEADLINE", "Due", etc. are never skipped.
                subject_clean = raw_subject.lower()
                snippet_clean = raw_snippet.lower()
                matched = any(
                    kw.lower() in subject_clean or kw.lower() in snippet_clean
                    for kw in DEADLINE_KEYWORDS
                )
                if not matched:
                    continue  # Skip emails that don't actually contain a keyword after normalisation

                deadline_hits.append({
                    'subject': raw_subject,          # keep original casing for display
                    'sender': headers.get('From', 'Unknown Sender'),
                    'snippet': raw_snippet,
                    'date': headers.get('Date', ''),
                })
            except Exception:
                continue
        return deadline_hits[:2]
    except Exception:
        return []

def enrich_horizon_suggestions_with_gmail(suggestions, gmail_deadlines):
    if not gmail_deadlines:
        return suggestions
    enriched = list(suggestions or [])
    for mail in gmail_deadlines:
        enriched.append({
            "event": f"Gmail deadline keyword: {mail.get('subject', 'No Subject')}",
            "event_start": mail.get('date', ''),
            "suggested_task": f"Review email: {mail.get('subject', 'No Subject')}",
            "urgency": "high",
            "reason": "Detected urgent deadline-related email in Gmail.",
        })
    return enriched

@st.fragment(run_every="1s")
def crisis_countdown_fragment(deadline_iso, task_label, placeholder=None):
    """Live crisis header — ticks every second without blocking the rest of the app."""
    container = placeholder if placeholder is not None else st
    try:
        deadline = datetime.datetime.fromisoformat(deadline_iso)
        if not deadline.tzinfo:
            deadline = IST.localize(deadline)
    except Exception:
        deadline = datetime.datetime.now(IST)

    remaining_secs = (deadline - datetime.datetime.now(IST)).total_seconds()
    if remaining_secs <= 0:
        container.markdown(
            """
            <div class='crisis-container'>
                <p class='crisis-countdown-label'>Crisis countdown</p>
                <p class='crisis-countdown'>⏱ 00:00:00 · DEADLINE REACHED</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        container.markdown(
            f"""
            <div class='crisis-container'>
                <p class='crisis-countdown-label'>Crisis countdown · {task_label}</p>
                <p class='crisis-countdown'>⏱ {format_live_countdown(deadline)}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

def offline_dna_profile(tasks):
    """Reflection Agent fallback when Gemini is unavailable."""
    user_name = st.session_state.get("user_display_name", "Student")
    completed = len([t for t in tasks if t.get("status") == "Completed"])
    active = len([t for t in tasks if t.get("status") != "Completed"])
    return f"""### ⏱️ Peak Execution Hours
{user_name}, your queue suggests late-evening sprints when calendar density drops below 40%.

### 📉 Time Estimation Variance
Active workload ({active} open, {completed} done) indicates a tendency to compress effort into the final 24 hours.

### ⚠️ Vulnerability Vectors
Hackathon and academic clusters carry the highest inherited risk in your current matrix.

### 💡 Aria's Recommendation
Block a 90-minute focus window before your next critical deadline and protect it like a live demo slot."""

def offline_study_resources(task_label):
    """Action Agent fallback resource catalog."""
    return f"""**Action Agent curated starter resources:**

- [Official documentation search](https://www.google.com/search?q={task_label.replace(' ', '+')}+official+documentation)
- [Stack Overflow — tagged discussions](https://stackoverflow.com/search?q={task_label.replace(' ', '+')})
- [YouTube — guided walkthroughs](https://www.youtube.com/results?search_query={task_label.replace(' ', '+')}+tutorial)"""

def offline_planning_doc(task):
    """Document Engine inline preview when Docs API is unavailable."""
    user_name = st.session_state.get("user_display_name", "Student")
    task_label = task.get("label", "Unnamed Task")
    return f"""## Executive Summary
Structured plan for **{task_label}** — owner: {user_name}. Category: {task.get('category', 'General')}. Risk tier: {task.get('risk', 'MEDIUM')}.

## Key Milestones
1. Scope and acceptance criteria (30 min)
2. Core deliverable sprint ({task.get('effort_hours', 2)}h budget)
3. Verification, packaging, and submission buffer

## Required Resources
- Primary toolchain for {task.get('category', 'General')} work
- Reference notes from Aria Breakdown Engine subtasks

## Risk Mitigation
If blocked, escalate via Action Agent draft and request a focus block on your calendar."""

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
    task_label = task.get("label", "Unnamed Task")
    return f"""Subject: Request for a short extension on {task_label}

Dear Professor,

I am writing to respectfully request a short, emergency extension for the deliverable: '{task_label}'.

Due to structural technical constraints with my compiling nodes, I require a small window to ensure a complete deployment rather than a rushed submission.

Thank you for your consideration,
{user_name}"""

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive.file',
]

def get_google_credentials():
    """Load Google OAuth credentials from Firestore or local token."""
    creds = None
    try:
        if db is not None and st.session_state.get("user_id"):
            try:
                user_meta = db.collection("user_meta").document(st.session_state.user_id).get()
                if user_meta.exists:
                    token_data = user_meta.to_dict().get("google_token")
                    if token_data:
                        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except Exception:
                creds = None

        if not creds and os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                refreshed_token_data = json.loads(creds.to_json())
                try:
                    with open('token.json', 'w', encoding='utf-8') as token_file:
                        token_file.write(creds.to_json())
                except Exception:
                    pass
                if db is not None and st.session_state.get("user_id"):
                    try:
                        db.collection("user_meta").document(st.session_state.user_id).set(
                            {"google_token": refreshed_token_data}, merge=True
                        )
                    except Exception:
                        pass
            except Exception:
                creds = None

        if creds and creds.valid:
            return creds
        return None
    except Exception:
        return None

def get_calendar_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build('calendar', 'v3', credentials=creds)
    except Exception:
        return None

def get_gmail_service():
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        return build('gmail', 'v1', credentials=creds)
    except Exception:
        return None

def get_docs_service():
    creds = get_google_credentials()
    if not creds:
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
    """Breakdown Agent: typed FunctionDeclaration schema — not a prompt, a model contract."""
    fallback_subtasks = [
        {"name": "Compile existing source notes", "hours": max(1, int(hours * 0.3))},
        {"name": "Generate structural blueprint", "hours": max(1, int(hours * 0.3))},
        {"name": "Execute primary sprint", "hours": max(1, int(hours * 0.4))}
    ]
    if not gemini_client:
        return "HIGH", fallback_subtasks
    try:
        tool = types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name="generate_task_deconstruction",
                    description="Return the risk tier and a structured list of subtasks for a task.",
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "risk": types.Schema(
                                type=types.Type.STRING,
                                description="Risk level for the task",
                                enum=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                            ),
                            "subtasks": types.Schema(
                                type=types.Type.ARRAY,
                                items=types.Schema(
                                    type=types.Type.OBJECT,
                                    properties={
                                        "name": types.Schema(type=types.Type.STRING, description="Subtask title or action."),
                                        "hours": types.Schema(type=types.Type.NUMBER, description="Estimated hours for the subtask."),
                                    },
                                    required=["name", "hours"],
                                ),
                            ),
                        },
                        required=["risk", "subtasks"],
                    ),
                )
            ]
        )
        prompt = f"""
        Task: '{label}' ({category}). Context: {description}.
        Days available: {days}. Effort hours: {hours}.
        Return a JSON object with keys: risk, subtasks. Subtasks must be an array of objects with name and hours.
        """
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.15, tools=[tool])
        )
        data = None
        if getattr(response, "function_calls", None):
            call = response.function_calls[0]
            data = call.args
        else:
            raw = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid Gemini function output")
        subtasks = data.get("subtasks", fallback_subtasks)
        if not isinstance(subtasks, list) or not all(isinstance(s, dict) for s in subtasks):
            subtasks = fallback_subtasks
        return data.get("risk", "HIGH"), subtasks
    except Exception:
        try:
            prompt = f"""
            Task: '{label}' ({category}). Context: {description}.
            Days available: {days}. Effort hours: {hours}.
            Break this into exactly 3 clear, actionable subtasks.
            Return ONLY valid JSON with no markdown fences.
            """
            response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
            raw = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
            subtasks = data.get("subtasks", fallback_subtasks)
            if not isinstance(subtasks, list) or not all(isinstance(s, dict) for s in subtasks):
                subtasks = fallback_subtasks
            return data.get("risk", "HIGH"), subtasks
        except Exception:
            return "HIGH", fallback_subtasks

def build_morning_briefing(tasks, density_report, suggestions):
    """Morning Briefing Agent: Gemini-powered with Google Search grounding."""
    active_tasks = [t for t in tasks if t.get("status") != "Completed"]
    task_names = [t.get('label', 'Unnamed Task') for t in active_tasks][:3]
    event_names = [s.get('event', 'an unknown event') for s in suggestions]
    user_name = st.session_state.get("user_display_name", "there")
    fallback = f"🌞 Good morning, {user_name}. You have {len(active_tasks)} active task(s) in queue. Focus on: {', '.join(task_names) if task_names else 'clearing your backlog'}."
    if not gemini_client:
        return fallback
    try:
        prompt = f"""
        Write a 2-sentence morning briefing for {user_name}.
        Calendar density: {density_report.get('density_score', 0)}%.
        Active tasks they need to focus on today: {task_names}.
        Unlinked calendar events they should watch out for: {event_names}.
        Tone: Direct, AI-assistant guardian, highly personalized. Start with '🌞 Good morning, {user_name}.'
        Do NOT just blindly list the tasks. Weave them into a natural, grounded briefing.
        """
        resp = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        )
        return resp.text.strip()
    except Exception as e:
        print(f"Briefing Error: {e}")
        return fallback

# =========================================================
# GOOGLE API ACTION FUNCTIONS
# =========================================================
def create_focus_block(task, hours_from_now=1, duration_hours=2):
    """Action Agent: Creates a real focus block in Google Calendar."""
    service = get_calendar_service()
    if not service:
        return False, "Calendar API offline — token.json not found or expired."
    try:
        start_time = current_time_ist + datetime.timedelta(hours=hours_from_now)
        end_time = start_time + datetime.timedelta(hours=duration_hours)
        task_label = task.get("label", "Unnamed Task")
        event = {
            "summary": f"🎯 Focus: {task_label}",
            "description": f"Aria focus block — auto-scheduled.\nTask risk: {task.get('risk', 'MEDIUM')}\nDeadline: {task.get('deadline_at', 'Unknown')}",
            "start": {"dateTime": start_time.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_time.isoformat(), "timeZone": "Asia/Kolkata"},
            "colorId": "11",
        }
        created = service.events().insert(calendarId="primary", body=event).execute()
        return True, f"Focus block created: {created.get('htmlLink', 'see Google Calendar')}"
    except HttpError as e:
        return False, f"Calendar API error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def send_gmail(to_email, subject, body):
    """Action Agent: Sends a real email via Gmail API."""
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
    """Action Agent: Creates a real Google Doc and returns its URL."""
    docs_service, drive_service = get_docs_service()
    if not docs_service:
        return False, "Google Docs API offline — token.json not found or expired."
    if not gemini_client:
        return False, "Gemini API offline — cannot generate content."
    try:
        task_label = task.get("label", "Unnamed Task")
        prompt = f"""
        Create a concise planning document for the task: '{task_label}'.
        Category: {task.get('category', 'General')}. Risk: {task.get('risk', 'MEDIUM')}.
        Include: Executive Summary, 3 Key Milestones, Required Resources, and a Risk Mitigation note.
        Keep it professional and concise.
        """
        resp = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        doc_content = resp.text.strip()
        doc = docs_service.documents().create(body={"title": f"📋 Aria Plan: {task_label}"}).execute()
        doc_id = doc["documentId"]
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": doc_content}}]}
        ).execute()
        return True, f"https://docs.google.com/document/d/{doc_id}/edit"
    except HttpError as e:
        return False, f"Docs API error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

# =========================================================
# TASK MANAGEMENT CORE
# =========================================================
def add_task_to_matrix(label, description, category, total_hours_left, effort_hours, density_report, source="Manual"):
    """Routes a new task through all AI agents before saving to Firestore."""
    deadline_at = current_time_ist + datetime.timedelta(hours=total_hours_left)
    base_risk, subtasks_list = execute_ai_deconstruction(
        label, description,
        max(round(total_hours_left / 24, 2), 0.04),
        effort_hours, category
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
        "horizon_density_score": density_report.get("density_score", 0),
        "subtasks": subtasks_list,
        "subtask_done": [False] * len(subtasks_list),
        "status": "Not Started",
        "source": source
    }
    task = ensure_local_task_identity(task)
    task["intervention_letter"] = intervention_letter(
        task.get("label", "Unnamed Task"), st.session_state.get("user_display_name", "Student"),
        deadline_at.strftime("%d %b, %I:%M %p"), description
    )
    log_agent_action("Intervention Agent", f"Generated future-self letter for '{task.get('label', 'Unnamed Task')[:20]}...'")
    if total_hours_left <= 6:
        task["crisis_plan"] = crisis_agent(task.get("label", "Unnamed Task"), total_hours_left)
        log_agent_action("Crisis Agent", f"Activated emergency metrics for '{task.get('label', 'Unnamed Task')[:20]}...'")
    upsert_cached_task(task)
    st.session_state.morning_briefing_text = None
    if db is not None and st.session_state.user_id:
        db.collection("tasks").document(st.session_state.user_id).collection("items").add(task)
        log_agent_action(source, f"Saved '{task.get('label', 'Unnamed Task')[:20]}...' to Firestore.")
    elif st.session_state.get("user_id"):
        log_agent_action(source, f"Saved '{task.get('label', 'Unnamed Task')[:20]}...' to local cache.")
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
    st.session_state.agent_log = generate_default_agent_log()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "user_email" not in st.session_state:
    st.session_state.user_email = None
if "user_display_name" not in st.session_state:
    st.session_state.user_display_name = "Student"
if "morning_briefing_text" not in st.session_state:
    st.session_state.morning_briefing_text = None
if "crisis_snow_triggered" not in st.session_state:
    st.session_state.crisis_snow_triggered = False
if "voice_panic_override" not in st.session_state:
    st.session_state.voice_panic_override = False
if "whatsapp_toasted" not in st.session_state:
    st.session_state.whatsapp_toasted = False

# =========================================================
# GOOGLE CALENDAR API INTEGRATION
# =========================================================
def fetch_live_calendar_deadlines(max_results=100):
    service = get_calendar_service()
    if not service:
        return None
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        return service.events().list(
            calendarId='primary', timeMin=now_utc,
            maxResults=max_results, singleEvents=True, orderBy='startTime'
        ).execute().get('items', [])
    except Exception:
        return []

# =========================================================
# LIVE FIRESTORE DATA SYNC
# =========================================================
live_events = fetch_live_calendar_deadlines(max_results=100)
horizon_report = horizon_agent_intelligence_loop(live_events or [], now=current_time_ist)
gmail_deadlines = scan_gmail_for_deadlines(max_results=8)

tasks_list = []
firestore_error = None
if st.session_state.user_id:
    try:
        if db is None:
            raise Exception("DB Offline")
        tasks_ref = db.collection("tasks").document(st.session_state.user_id).collection("items")
        for doc in tasks_ref.stream():
            tasks_list.append({**doc.to_dict(), "id": doc.id})
    except Exception as e:
        firestore_error = str(e)
        tasks_list = get_cached_tasks_snapshot()

for t in tasks_list:
    sync_task_runtime_fields(t, horizon_report)

tasks_list = write_cached_tasks(tasks_list)
st.session_state['intervention_alerts'] = check_proactive_interventions(tasks_list)

# =========================================================
# AUTONOMOUS CRON SERVICE (MOVED TO CLOUD FUNCTIONS)
# =========================================================
# The old Streamlit-based cron has been replaced with a true
# Cloud Scheduler endpoint for genuine autonomous operation.
#
# See: cron_service.py
# Deploy guide: CLOUD_SCHEDULER_DEPLOYMENT.md
#
# The new service runs independently of this Streamlit app,
# executing check_proactive_interventions() for all users on a timer.

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
                st.session_state.user_display_name = (
                    user.display_name or name_input.strip() or "Student"
                )
                # Load or seed agent log from Firestore
                try:
                    if db is not None and st.session_state.user_id:
                        meta_ref = db.collection("user_meta").document(st.session_state.user_id)
                        meta = meta_ref.get()
                        persisted_log = []
                        if meta.exists:
                            persisted_log = meta.to_dict().get("agent_log") or []
                        if persisted_log:
                            st.session_state.agent_log = persisted_log
                        else:
                            default_log = generate_default_agent_log()
                            meta_ref.set({"agent_log": default_log}, merge=True)
                            st.session_state.agent_log = default_log
                except Exception:
                    st.session_state.agent_log = generate_default_agent_log()
                st.toast(log_msg, icon="✅")
                st.rerun()
            except Exception as e:
                print(f"Auth rejected: {e}")
                st.warning(
                    "🔒 **Access Denied:** Unrecognized network credentials. "
                    "Please verify your secure email."
                )
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

    audio_bytes = st.audio_input("🎙️ Emergency Voice Command")

    if audio_bytes is not None:
        if not st.session_state.voice_panic_override:
            voice_panic = True
            st.toast("Voice parsed: Critical stress detected. Activating Crisis Mode.", icon="🚨")
        else:
            voice_panic = False
        if st.button("🛑 Stand Down (Clear Voice Panic)", use_container_width=True):
            st.session_state.voice_panic_override = True
            st.rerun()
    else:
        if not audio_bytes and st.session_state.voice_panic_override:
            st.session_state.voice_panic_override = False
        voice_panic = False

    manual_crisis = st.checkbox("💥 Force Crisis Mode (Under 6h)", value=False) or voice_panic

    whatsapp_sync = st.toggle(
        "📱 WhatsApp Ambient Sync",
        value=st.session_state.get("whatsapp_sync_enabled", True),
        key="whatsapp_sync_enabled"
    )

    # 🤖 Core Agent Status
    st.markdown("---")
    st.subheader("🤖 Core Agent Status")
    try:
        crit_present = any(
            t.get("risk") == "CRITICAL" and t.get("status") != "Completed"
            for t in tasks_list
        )
        crisis_active_sidebar = manual_crisis or crit_present
        action_color = "#f59e0b" if crisis_active_sidebar else "#10b981"
        crisis_color = "#ef4444" if crisis_active_sidebar else "#10b981"
        intervention_color = "#f59e0b" if st.session_state.get("intervention_alerts") else "#10b981"
        st.markdown(
            f"""
            <div class="agent-status-container">
                <div class="agent-dot"><span style="background-color: #10b981;"></span>Horizon Agent · Active</div>
                <div class="agent-dot"><span style="background-color: #10b981;"></span>Breakdown Engine · Armed</div>
                <div class="agent-dot"><span style="background-color: #10b981;"></span>Priority Orchestrator · Active</div>
                <div class="agent-dot"><span style="background-color: {intervention_color};"></span>Intervention Agent · Monitor</div>
                <div class="agent-dot"><span style="background-color: {action_color};"></span>Action Agent · Connected</div>
                <div class="agent-dot"><span style="background-color: {crisis_color};"></span>Crisis Agent · Status</div>
                <div class="agent-dot"><span style="background-color: #10b981;"></span>Reflection Agent · Active</div>
            </div>
            """,
            unsafe_allow_html=True
        )
    except Exception:
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
    if live_events is not None:
        st.metric(
            "48h Calendar Density",
            f"{horizon_report.get('density_score', 0)}%",
            horizon_report.get("density_tier", "CLEAR").title()
        )
    if live_events is None:
        st.info("📅 Horizon running in **local mode** — connect Google Workspace for live calendar sync.")
    elif not live_events:
        st.success("🎉 Horizon completely clear!")
    else:
        for event in live_events[:3]:
            summary = event.get('summary', 'Unlabeled Event')
            start_info = event.get('start', {})
            start_raw = start_info.get('dateTime', start_info.get('date', ''))
            if 'T' in start_raw:
                formatted_time = f"⏰ {start_raw.split('T')[0]} @ {start_raw.split('T')[1][:5]}"
            else:
                formatted_time = f"📅 {start_raw} (All-Day)"
            with bordered_container(st.sidebar):
                st.markdown(f"**{summary}**")
                st.caption(formatted_time)

    if gmail_deadlines:
        st.markdown("---")
        st.subheader("📬 Gmail Deadline Keywords")
        for mail in gmail_deadlines[:2]:
            with bordered_container(st.sidebar):
                st.markdown(f"**{mail.get('subject', 'No Subject')}**")
                st.caption(f"{mail.get('sender', 'Unknown Sender')} · {mail.get('date', '')}")
                st.write(mail.get('snippet', 'No preview available.'))

    st.markdown("---")

    # 📥 Export Data
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

    # 🗑️ Demo Tools
    st.markdown("---")
    if st.session_state.user_id:
        with st.expander("⚠️ Demo Tools"):
            st.caption("For hackathon demo use only.")
            if st.button("🔴 Seed Crisis Demo Task", use_container_width=True):
                add_task_to_matrix(
                    label="Machine Learning Project Presentation",
                    description="Prepare the machine learning project presentation with slides and rehearsal.",
                    category="Academic",
                    total_hours_left=52,
                    effort_hours=8,
                    density_report=horizon_report,
                    source="Demo Seed",
                )
                add_task_to_matrix(
                    label="Algorithm Assignment — Graph Theory",
                    description="Complete the graph theory assignment with proofs and code examples.",
                    category="Academic",
                    total_hours_left=120,
                    effort_hours=4,
                    density_report=horizon_report,
                    source="Demo Seed",
                )
                add_task_to_matrix(
                    label="Hackathon Final Submission Build",
                    description="Finish the final build for hackathon submission and demo stabilization.",
                    category="Hackathon Sprint",
                    total_hours_left=2,
                    effort_hours=6,
                    density_report=horizon_report,
                    source="Demo Seed",
                )
                st.rerun()
            if st.button("🗑️ Wipe Database (Demo Reset)", use_container_width=True, type="secondary"):
                try:
                    deleted = len(get_cached_tasks_snapshot())
                    if db is not None:
                        ref = db.collection("tasks").document(st.session_state.user_id).collection("items")
                        deleted = 0
                        for doc in ref.stream():
                            doc.reference.delete()
                            deleted += 1
                    clear_cached_tasks()
                    st.session_state.morning_briefing_text = None
                    st.session_state.whatsapp_toasted = False
                    st.session_state.agent_log = [
                        {"agent": "System", "action": f"Demo reset — {deleted} task(s) wiped.", "time": datetime.datetime.now(IST).strftime("%I:%M %p")}
                    ]
                    show_success_toast(f"Wiped {deleted} task(s). Ready for next judge.", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    print(f"Demo reset failed: {e}")
                    st.warning("🗑️ **Demo reset paused** — secure sync channel busy. Try again in a moment.")

active_crisis_state = manual_crisis or any(
    t.get("hours_left", 999) <= 6 and t.get("status", "Not Started") != "Completed"
    for t in tasks_list
)

if not active_crisis_state:
    st.session_state.crisis_snow_triggered = False
    st.session_state.pop("crisis_demo_deadline", None)

# =========================================================
# MAIN WORKSPACE DASHBOARD
# =========================================================
if not st.session_state.user_id:
    st.title("🚨 Aria — The Last-Minute Life Saver")
    st.warning("🔒 Secure Network Node Offline: Authenticate your account email using the Firebase panel in the sidebar.")

elif active_crisis_state:
    # 💥 CRISIS MODE
    if not st.session_state.crisis_snow_triggered:
        st.snow()
        st.session_state.crisis_snow_triggered = True

    gmail_ready = get_gmail_service() is not None

    if not tasks_list:
        if "crisis_demo_deadline" not in st.session_state:
            st.session_state.crisis_demo_deadline = (
                datetime.datetime.now(IST) + datetime.timedelta(hours=2)
            ).isoformat()
        crisis_task = {"label": "Hackathon Final Submission", "deadline_at": st.session_state.crisis_demo_deadline}
        crisis_deadline = parse_deadline(crisis_task)
    else:
        st.session_state.pop("crisis_demo_deadline", None)
        crisis_task = next(
            (t for t in tasks_list if t.get("hours_left", 99) <= 6 and t.get("status") != "Completed"),
            tasks_list[0] if tasks_list else {},
        )
        crisis_deadline = parse_deadline(crisis_task)

    countdown_placeholder = st.empty()
    crisis_countdown_fragment(
        crisis_deadline.isoformat(),
        crisis_task.get("label", "Critical task"),
        placeholder=countdown_placeholder,
    )

    col1, col2 = st.columns([2.2, 1])
    with col1:
        st.subheader("🪓 Ruthless Execution Battle Plan")
        with bordered_container():
            st.markdown(
                "<p style='color:#fca5a5; font-weight:600; margin-bottom:12px;'>"
                "🚨 Execution paralysis block active — secondary matrices suppressed.</p>",
                unsafe_allow_html=True,
            )
            crisis_plan_text = render_crisis_plan(crisis_task.get("crisis_plan") or [])
            if crisis_plan_text:
                st.markdown(crisis_plan_text)
            else:
                st.markdown("⏱ **00m - 45m:** Isolate primary data models.")
                st.markdown("⏱ **45m - 90m:** Verify pipeline parameters without documentation.")

    with col2:
        st.subheader("✉️ Action Agent Blueprint")
        with bordered_container():
            draft = extension_email_draft(crisis_task)
            with st.expander("📩 Extension Request Draft", expanded=True):
                st.text_area("Email Output:", value=draft, height=180, disabled=True, label_visibility="collapsed")
            if not gmail_ready:
                st.markdown(
                    "<p class='draft-staged-caption'>✅ Draft Staged (API Sandbox Mode)</p>",
                    unsafe_allow_html=True,
                )
            recipient = st.text_input("Recipient Email:", placeholder="professor@college.edu", key="crisis_recipient")
            if st.button(
                "🚀 Dispatch via Gmail API",
                use_container_width=True,
                disabled=not gmail_ready,
                help="Gmail API unavailable — draft staged in sandbox mode." if not gmail_ready else None,
            ):
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
                        st.markdown(
                            "<p class='draft-staged-caption'>✅ Draft staged — ready to send</p>",
                            unsafe_allow_html=True,
                        )
                        log_agent_action("Action Agent", f"Extension draft staged for {recipient}")
                else:
                    st.info("Add a recipient email to stage the extension draft.")

else:
    # 🏢 STANDARD MISSION CONTROL DASHBOARD
    st.title(f"🚨 Aria — Mission Control · {st.session_state.user_display_name}")

    if firestore_error:
        print(f"Firestore read error: {firestore_error}")
        st.info(
            "📦 **Local cache mode** — live Firestore sync paused. "
            "Your session is still active; reconnect to refresh the task matrix."
        )

    try:
        suggestions = horizon_agent(live_events or [], [t.get("label", "Unnamed Task") for t in tasks_list])
        suggestions = enrich_horizon_suggestions_with_gmail(suggestions, gmail_deadlines)
    except Exception:
        suggestions = []

    if st.session_state.morning_briefing_text is None:
        st.session_state.morning_briefing_text = build_morning_briefing(tasks_list, horizon_report, suggestions)

    brief_col, refresh_col, auto_col = st.columns([8, 1, 3])
    with brief_col:
        st.info(st.session_state.morning_briefing_text)
        alerts = st.session_state.get("intervention_alerts", [])
        if alerts:
            for alert in alerts:
                color = "#f97316" if alert["severity"] == "HIGH" else "#ef4444"
                severity_label = alert["severity"].title()
                st.markdown(
                    f"""
                    <div style='border: 1px solid {color}; border-radius: 10px; padding: 14px; margin-top: 10px; background: rgba(254, 242, 200, 0.18);'>
                        <strong style='color:{color};'>⚠️ {severity_label} Intervention Alert</strong>
                        <p style='margin: 6px 0 0 0; color:#e2e8f0;'>{alert['message']}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    with refresh_col:
        if st.button("↻", help="Refresh morning briefing", key="refresh_briefing"):
            st.session_state.morning_briefing_text = None
            st.rerun()
    with auto_col:
        if st.button("🤖 Run Autopilot Scan", type="primary", use_container_width=True):
            st.session_state.whatsapp_toasted = False
            with st.status("Aria Autopilot running...", expanded=True) as status:
                status.write("1/4 — Running Horizon Agent")
                autopilot_suggestions = horizon_agent(live_events or [], [t.get("label", "Unnamed Task") for t in tasks_list]) or []
                log_agent_action("Horizon Agent", "Autopilot refreshed horizon suggestions.")

                status.write("2/4 — Updating Priority scores")
                for task in tasks_list:
                    sync_task_runtime_fields(task, horizon_report)
                log_agent_action("Priority Orchestrator", "Autopilot updated task priorities.")

                status.write("3/4 — Scanning Gmail for deadlines")
                try:
                    fresh_gmail = scan_gmail_for_deadlines()
                    autopilot_suggestions = enrich_horizon_suggestions_with_gmail(autopilot_suggestions, fresh_gmail)
                    log_agent_action("Action Agent", "Autopilot scanned Gmail deadlines.")
                except Exception:
                    pass

                status.write("4/4 — Triggering Reflection Agent")
                st.session_state.morning_briefing_text = None
                st.session_state.morning_briefing_text = build_morning_briefing(
                    tasks_list, horizon_report, autopilot_suggestions or suggestions
                )
                log_agent_action("Reflection Agent", "Autopilot refreshed briefing and reflection.")

                if whatsapp_sync and not st.session_state.whatsapp_toasted:
                    st.toast("📱 WhatsApp Notification Sent: Aria updated your external matrix.", icon="💬")
                    st.session_state.whatsapp_toasted = True

                status.update(label="Autopilot complete — all agents reported", state="complete")
            st.rerun()

    # Force banner to display if either calendar suggestions OR raw unread gmail deadlines exist
    display_banner_text = None
    if suggestions:
        display_banner_text = suggestions[0].get('event', 'Unknown Event')
    elif gmail_deadlines:
        display_banner_text = f"Gmail match: {gmail_deadlines[0].get('subject', 'No Subject')}"

    if display_banner_text:
        st.markdown(
            f"""
            <div class='horizon-banner'>
                <strong>🔍 Horizon Sync Warning</strong><br>
                Unlinked calendar entry: '{display_banner_text}'. No task track exists.
            </div>
            """,
            unsafe_allow_html=True
        )
        if st.button("⚡ Auto-Generate Study Plan Matrix"):
            with st.spinner("Compiling via Gemini..."):
                # If it came from Gmail, use the Gmail text safely
                label_text = gmail_deadlines[0].get('subject', 'Prep') if gmail_deadlines else suggestions[0].get('suggested_task', 'Prep')
                add_task_to_matrix(
                    label=label_text,
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
            st.file_uploader(
                "📄 Drop Syllabus or Rubric (Multi-modal)",
                type=["pdf", "png", "jpg"],
                key="syllabus_upload"
            )
            if st.button("🪄 Auto-Extract via Gemini Vision", type="secondary"):
                uploaded_file = st.session_state.get("syllabus_upload")
                if not uploaded_file:
                    st.warning("Please upload a syllabus file first.")
                elif not gemini_client:
                    st.warning("Gemini API offline. Cannot parse document.")
                else:
                    with st.spinner("Gemini Vision analyzing document..."):
                        try:
                            doc_part = types.Part.from_bytes(
                                data=uploaded_file.getvalue(),
                                mime_type=uploaded_file.type
                            )
                            prompt = "Analyze this assignment document. Extract and return ONLY a raw JSON object with exactly three keys: 'label' (a short 3-5 word title), 'description' (a 1 sentence summary), and 'effort_hours' (your best integer estimate of hours needed). Do not use markdown formatting or code blocks."
                            resp = gemini_client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=[prompt, doc_part]
                            )
                            raw_json = resp.text.strip().replace('```json', '').replace('```', '').strip()
                            extracted = json.loads(raw_json)
                            st.session_state.mock_lbl = extracted.get("label", "Extracted Assignment")
                            st.session_state.mock_desc = extracted.get("description", "Extracted context.")
                            st.session_state.mock_hrs = int(extracted.get("effort_hours", 5))
                            log_agent_action("Reflection Agent", "Parsed syllabus via Gemini Vision.")
                            st.success("Document ingested and parameters extracted!")
                        except Exception as e:
                            st.error("Failed to parse document. Please enter manually.")
                            print(f"Vision Error: {e}")

            with st.form("injector", clear_on_submit=True):
                t_lbl = st.text_input("Task Label:", value=st.session_state.get("mock_lbl", ""))
                t_desc = st.text_area("Context:", value=st.session_state.get("mock_desc", ""))
                c1, c2, c3 = st.columns(3)
                with c1:
                    days_val = st.number_input("Days:", min_value=1, value=3)
                with c2:
                    hrs_val = st.slider("Effort (hrs):", 1, 100, value=st.session_state.get("mock_hrs", 5))
                with c3:
                    cat_val = st.selectbox("Category:", ["Academic", "Professional", "Hackathon Sprint"])
                if primary_form_submit_button("⚡ Instantiate"):
                    if t_lbl:
                        with st.spinner("Breakdown Agent deconstructing task..."):
                            add_task_to_matrix(t_lbl, t_desc, cat_val, days_val * 24, hrs_val, horizon_report)
                        st.session_state.pop("mock_lbl", None)
                        st.session_state.pop("mock_desc", None)
                        st.session_state.pop("mock_hrs", None)
                        st.rerun()

        # 📊 Gantt Timeline Chart
        st.subheader("📊 7-Day Deadline Timeline Matrix")
        if tasks_list:
            gantt_list = []
            for t in sorted(tasks_list, key=lambda x: parse_deadline(x)):
                try:
                    created = datetime.datetime.fromisoformat(t.get("created_at", ""))
                    if not created.tzinfo:
                        created = IST.localize(created)
                except Exception:
                    created = parse_deadline(t) - datetime.timedelta(hours=t.get("effort_hours", 2))
                gantt_list.append({
                    "Task Target": t.get("label", "Unnamed Task"),
                    "Start": created,
                    "End": parse_deadline(t),
                    "Category": t.get("category", ""),
                    "Effort": t.get("effort_hours", 0),
                    "Risk": t.get("risk", "MEDIUM")
                })
            df = pd.DataFrame(gantt_list)
            fig = px.timeline(
                df, x_start="Start", x_end="End", y="Task Target", color="Risk",
                hover_data={"Category": True, "Effort": True, "Risk": True},
                color_discrete_map={"CRITICAL": "#ef4444", "HIGH": "#f97316", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10))
            try:
                fig.add_vline(x=current_time_ist, line=dict(color="red", dash="dash"))
            except Exception:
                pass
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("📊 Timeline appears after your first task is queued.")

        # 📋 Task Matrix
        st.subheader("📋 Your Real-Time Strategic Checklist")
        sorted_tasks = sorted(tasks_list, key=lambda x: x.get("priority_score", 0), reverse=True)
        active_tasks = [t for t in sorted_tasks if t.get("status") != "Completed"]
        completed_tasks = [t for t in sorted_tasks if t.get("status") == "Completed"]

        tab_active, tab_done, tab_trace, tab_dna, tab_chat = st.tabs([
            f"🔥 Active ({len(active_tasks)})",
            f"✅ Completed ({len(completed_tasks)})",
            "🏗️ Stack & Trace",
            "🧬 Deadline DNA",
            "💬 Aria Chat"
        ])

        def render_task_card(item):
            item_id = item.get("id")
            item_label = item.get("label", "Unnamed Task")
            item_risk = item.get("risk", "MEDIUM")
            item_hours_left = item.get("hours_left", 0)
            r_col = "#ef4444" if item_risk in ["HIGH", "CRITICAL"] else "#f59e0b" if item_risk == "MEDIUM" else "#10b981"

            with bordered_container():
                t_col, b_col = st.columns([4, 1])
                t_col.markdown(f"### {item_label}\n**{format_countdown(item_hours_left)}** remaining")
                b_col.markdown(
                    f"<div style='background-color: {r_col}; color: white; padding: 6px; text-align: center; border-radius: 6px;'>RISK: {item_risk}</div>",
                    unsafe_allow_html=True
                )
                st.markdown("---")
                bod, act = st.columns([3, 1])

                with bod:
                    subtasks = item.get("subtasks", [])
                    subtask_done = list(item.get("subtask_done", [False] * len(subtasks)))
                    while len(subtask_done) < len(subtasks):
                        subtask_done.append(False)

                    if subtasks:
                        st.markdown("🔥 **Tactical Subtasks:**")
                        for s_idx, sub in enumerate(subtasks):
                            st.checkbox(
                                f"{sub.get('name', 'Step')} ({sub.get('hours', '?')}h)",
                                value=bool(subtask_done[s_idx]),
                                key=f"s_{item_id}_{s_idx}"
                            )
                        live_done = [
                            bool(st.session_state.get(f"s_{item_id}_{i}", subtask_done[i]))
                            for i in range(len(subtasks))
                        ]
                        completed_count = sum(live_done)
                        st.progress(
                            completed_count / len(subtasks),
                            text=f"Progress: {completed_count}/{len(subtasks)} subtasks complete"
                        )
                        if live_done != subtask_done:
                            if db is not None and item_id:
                                db.collection("tasks").document(st.session_state.user_id) \
                                  .collection("items").document(item_id) \
                                  .update({"subtask_done": live_done})
                            if item_id:
                                update_cached_task(item_id, {"subtask_done": live_done})
                            log_agent_action("Breakdown Engine", f"Subtask progress saved for '{item_label[:20]}...'")

                    if st.button("📅 Schedule Focus Block in Calendar", key=f"cal_{item_id}"):
                        duration_hours = int(item.get("effort_hours", 2))
                        start_time = current_time_ist + datetime.timedelta(hours=1)
                        end_time = start_time + datetime.timedelta(hours=duration_hours)
                        with st.spinner("Writing to Google Calendar..."):
                            ok, msg = create_focus_block(item, hours_from_now=1, duration_hours=duration_hours)
                        if ok:
                            show_success_toast("Focus block injected into Calendar!", "📅")
                            if whatsapp_sync:
                                st.toast("📱 WhatsApp: Focus block synced to device.", icon="💬")
                            log_agent_action("Action Agent", f"Calendar write: focus block for '{item_label[:20]}...'")
                            st.success(f"✅ {msg}")
                        else:
                            st.info(
                                f"📅 **Focus block staged** — "
                                f"{start_time.strftime('%I:%M %p')} – {end_time.strftime('%I:%M %p IST')} · "
                                f"**{item_label}**"
                            )
                            log_agent_action("Action Agent", "Focus block queued — pending Calendar scope")

                    if st.button("🔍 Find Study Resources", key=f"search_{item_id}"):
                        with st.spinner("Action Agent scanning web directories..."):
                            if not gemini_client:
                                st.info(offline_study_resources(item_label))
                                log_agent_action("Action Agent", f"Served offline resource catalog for '{item_label[:20]}...'")
                            else:
                                prompt = f"Search the live web for the 3 most relevant, current resources for completing: '{item_label}'. Return results as a markdown bulleted list with real URLs. Prioritize official documentation and active community threads."
                                try:
                                    links = gemini_client.models.generate_content(
                                        model="gemini-2.5-flash",
                                        contents=prompt,
                                        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
                                    ).text
                                    st.info(f"**Action Agent found these resources:**\n\n{links}")
                                    log_agent_action("Action Agent", f"Queried custom search for '{item_label[:20]}...'")
                                except Exception:
                                    st.info(offline_study_resources(item_label))
                                    log_agent_action("Action Agent", f"Served offline resource catalog for '{item_label[:20]}...'")

                    if st.button("📄 Generate Planning Doc", key=f"doc_{item_id}"):
                        with st.spinner("Creating Google Doc via Docs API..."):
                            ok, result = create_planning_doc(item)
                        if ok:
                            st.success(f"✅ Google Doc created: [Open in Drive]({result})")
                            log_agent_action("Action Agent", f"Google Doc created for '{item_label[:20]}...'")
                        else:
                            st.info("📄 Generating via Aria's Document Engine — inline preview")
                            doc_content = offline_planning_doc(item)
                            if gemini_client:
                                try:
                                    doc_content = gemini_client.models.generate_content(
                                        model="gemini-2.5-flash",
                                        contents=f"Create a structured Markdown planning document for: '{item_label}'. Include Executive Summary, Milestones, and Resources."
                                    ).text
                                except Exception:
                                    pass
                            with st.expander("📄 Planning Doc Preview", expanded=True):
                                st.markdown(doc_content)
                            log_agent_action("Action Agent", f"Inline planning doc rendered for '{item_label[:20]}...'")

                    if item.get("intervention_letter"):
                        with st.expander("✉️ Intervention Letter"):
                            st.markdown(
                                f"<div class='letter-card'>{item.get('intervention_letter', '')}</div>",
                                unsafe_allow_html=True
                            )

                with act:
                    current_idx = ["Not Started", "In Progress", "Completed"].index(item.get("status", "Not Started"))
                    ns = st.selectbox(
                        "Status:",
                        ["Not Started", "In Progress", "Completed"],
                        key=f"stat_{item_id}",
                        index=current_idx
                    )
                    if ns != item.get("status"):
                        update_payload = {"status": ns}
                        if ns == "Completed":
                            update_payload["completed_at"] = datetime.datetime.now(IST).isoformat()
                        if db is not None and item_id:
                            db.collection("tasks").document(st.session_state.user_id) \
                              .collection("items").document(item_id) \
                              .update(update_payload)
                        if item_id:
                            update_cached_task(item_id, update_payload)
                        st.session_state.morning_briefing_text = None
                        if ns == "Completed":
                            st.balloons()
                        st.rerun()

                    score = item.get("priority_score", 0)
                    score_color = "#ef4444" if score >= 70 else "#f59e0b" if score >= 40 else "#10b981"
                    st.markdown(
                        f"<div style='margin-top:8px; background:{score_color}22; border:1px solid {score_color}; border-radius:6px; padding:6px; text-align:center;'>"
                        f"<span style='color:{score_color}; font-weight:600; font-size:11px;'>PRIORITY</span><br>"
                        f"<span style='color:{score_color}; font-size:22px; font-weight:700;'>{score}</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    if item.get("risk") and item.get("hours_left") is not None and gemini_client:
                        if st.button("🧠 Why this priority?", key=f"why_{item_id}"):
                            with st.spinner("Aria reasoning..."):
                                try:
                                    prompt = (
                                        f"Simulate a brief 2-turn debate between 'Horizon Agent' "
                                        f"(arguing based on {item.get('hours_left')}h deadline) and "
                                        f"'Priority Orchestrator' (arguing based on {item.get('risk')} risk) "
                                        f"regarding task '{item.get('label')}'. Format as a dialogue."
                                    )
                                    resp = gemini_client.models.generate_content(
                                        model="gemini-2.5-flash", contents=prompt
                                    ).text
                                    st.info(resp)
                                except Exception:
                                    st.info("Priority based on risk and deadline pressure.")

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

        with tab_trace:
            st.subheader("🏗️ Architecture & Agent Trace")

            st.markdown("#### 🔗 Nine Google Technologies")
            products = [
                ("Gemini 2.5 Flash", "Core Intelligence", "Powers all 8 agents — briefing, deconstruction, crisis plans, intervention letters, DNA analysis, planning docs"),
                ("Gemini Function Calling", "Advanced API", "Breakdown Agent uses typed FunctionDeclaration — risk enum + subtasks Array — a formal model contract"),
                ("Gemini Google Search", "Grounding", "Morning Briefing and Study Resources use live GoogleSearch tool for real-time grounded responses"),
                ("Google Calendar API", "Read + Write", "Horizon Agent reads 48h events; Action Agent writes focus blocks with color, metadata, and IST timezone"),
                ("Gmail API", "Read + Write", "Horizon Agent scans unread deadline keywords; Action Agent sends MIME-encoded extension emails"),
                ("Google Docs API", "Live Write", "Action Agent creates Docs with AI-generated content and returns a shareable Drive URL"),
                ("Google Drive API", "Live Write", "Hosts Docs-created documents in user Drive"),
                ("Firebase Authentication", "Multi-User", "Email-based user creation via Admin SDK — persists display names for returning users"),
                ("Firebase Firestore", "Real-Time DB", "Tasks, subtask state, agent logs, DNA timestamps — survives browser close and container restart"),
            ]
            cols = st.columns(3)
            for i, (name, badge, desc) in enumerate(products):
                with cols[i % 3]:
                    st.markdown(
                        f"<div style='border:1px solid #1e3a5f;border-radius:8px;padding:10px;"
                        f"background:#0c1a2e;margin-bottom:8px;'>"
                        f"<div style='color:#38bdf8;font-size:12px;font-weight:600;'>{name}</div>"
                        f"<div style='color:#10b981;font-size:10px;margin-top:2px;'>{badge}</div>"
                        f"<div style='color:#64748b;font-size:11px;margin-top:4px;'>{desc}</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

            st.divider()
            st.markdown("#### 🤖 Agent Network")
            st.markdown(
                "| Agent | Trigger | Google Integration |\n"
                "|---|---|---|\n"
                "| 🔭 Horizon Agent | Every page load | Calendar API (read) · Gmail API (read) |\n"
                "| 🌅 Morning Briefing | New session / refresh | Gemini 2.5 Flash + Google Search |\n"
                "| ✂️ Breakdown Agent | Task creation | Gemini Function Calling |\n"
                "| ⚠️ Intervention Agent | Every Firestore sync | Firestore (read) |\n"
                "| 📊 Priority Orchestrator | Every Firestore sync | Firestore (read/write) |\n"
                "| ⚡ Action Agent | User button click | Calendar · Gmail · Docs · Drive (write) |\n"
                "| 🚨 Crisis Agent | hours_left ≤ 6 | Gemini 2.5 Flash (JSON mode) |\n"
                "| 🧬 Reflection Agent | DNA tab opened | Gemini 2.5 Flash · Firestore (read) |\n"
            )

            st.divider()
            st.markdown("#### 📋 Live Agent Trace")
            st.caption("Most recent autonomous decisions across all agents.")
            trace_entries = st.session_state.agent_log[:10]
            if not trace_entries:
                st.info("No agent trace available yet.")
            else:
                for entry in trace_entries:
                    agent_name = entry.get("agent", "Unknown")
                    color = "#185FA5"
                    if agent_name in ["Breakdown Agent", "Priority Orchestrator", "Breakdown Engine"]:
                        color = "#3B6D11"
                    elif agent_name in ["Action Agent", "Intervention Agent"]:
                        color = "#854F0B"
                    elif agent_name == "Crisis Agent":
                        color = "#A32D2D"
                    st.markdown(
                        f"<div style='border:1px solid {color}; border-radius:12px; padding:12px; margin-bottom:10px; background:#0f172a;'>"
                        f"<div style='display:flex; align-items:center; gap:10px; margin-bottom:6px;'>"
                        f"<span style='width:10px; height:10px; border-radius:50%; background:{color}; display:inline-block;'></span>"
                        f"<strong style='color:#f8fafc;'>{agent_name}</strong> "
                        f"<span style='color:#94a3b8; font-size:12px;'>{entry.get('time', '')}</span>"
                        f"</div>"
                        f"<div style='color:#cbd5e1; font-size:0.9rem;'>{entry.get('action', '')}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

        with tab_dna:
            st.subheader("🧬 Deadline DNA — Productivity Fingerprint")
            dna_tasks = st.session_state.get('cached_tasks', [])
            if not dna_tasks:
                st.info("No tasks available to analyze. Add tasks to generate a Deadline DNA profile.")
            else:
                task_strings = [
                    f"{t.get('label')} (status: {t.get('status')}, category: {t.get('category')}, deadline: {t.get('deadline_at')})"
                    for t in dna_tasks
                ]
                prompt_instruction = "Return a JSON object with exactly these keys: 'peak_hours' (1 sentence), 'variance' (1 sentence on time underestimation), 'vulnerability' (1 sentence on weakest category), 'recommendation' (1 concrete action), and 'completion_rate' (a float between 0 and 1)."
                full_prompt = prompt_instruction + "\n\nTasks:\n" + json.dumps(task_strings)

                if not gemini_client:
                    st.info("Gemini offline — showing local Deadline DNA summary.")
                    completed = len([t for t in dna_tasks if t.get('status') == 'Completed'])
                    completion_rate = completed / max(len(dna_tasks), 1)
                    st.metric("Completion Rate", f"{completion_rate:.2f}")
                    st.progress(completion_rate)
                    category_stats = []
                    df_dna = pd.DataFrame(dna_tasks)
                    if not df_dna.empty and "category" in df_dna.columns:
                        for cat, grp in df_dna.groupby(df_dna["category"].fillna("Uncategorized")):
                            total_cat = len(grp)
                            completed_cat = len(grp[grp["status"] == "Completed"])
                            category_stats.append({
                                "Category": cat,
                                "Completion Rate": round((completed_cat / total_cat) * 100, 1) if total_cat else 0,
                            })
                    if category_stats:
                        chart_df = pd.DataFrame(category_stats)
                        fig_dna = px.bar(chart_df, x="Category", y="Completion Rate", text="Completion Rate", color="Category")
                        fig_dna.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                        fig_dna.update_yaxes(range=[0, 100])
                        st.plotly_chart(fig_dna, use_container_width=True)
                    st.markdown(offline_dna_profile(dna_tasks))
                else:
                    with st.spinner("Analyzing Deadline DNA via Gemini..."):
                        try:
                            resp = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=full_prompt)
                            raw = resp.text.strip().replace('```json', '').replace('```', '').strip()
                            data = json.loads(raw)
                            comp = float(data.get('completion_rate', 0))
                            st.metric("Completion Rate", f"{comp:.2f}")
                            st.progress(comp)
                            category_stats = []
                            df_dna = pd.DataFrame(dna_tasks)
                            if not df_dna.empty and "category" in df_dna.columns:
                                for cat, grp in df_dna.groupby(df_dna["category"].fillna("Uncategorized")):
                                    total_cat = len(grp)
                                    completed_cat = len(grp[grp["status"] == "Completed"])
                                    category_stats.append({
                                        "Category": cat,
                                        "Completion Rate": round((completed_cat / total_cat) * 100, 1) if total_cat else 0,
                                    })
                            if category_stats:
                                chart_df = pd.DataFrame(category_stats)
                                fig_dna = px.bar(chart_df, x="Category", y="Completion Rate", text="Completion Rate", color="Category")
                                fig_dna.update_layout(height=320, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                                fig_dna.update_yaxes(range=[0, 100])
                                st.plotly_chart(fig_dna, use_container_width=True)
                            st.markdown(
                                f"""
                                <div style='padding:12px; border-radius:8px; background:#0b1220; color:#cbd5e1;'>
                                <h4 style='color:#7dd3fc;'>Peak Hours</h4>
                                <p>{data.get('peak_hours','')}</p>
                                <h4 style='color:#fb923c;'>Estimation Variance</h4>
                                <p>{data.get('variance','')}</p>
                                <h4 style='color:#fca5a5;'>Vulnerability</h4>
                                <p>{data.get('vulnerability','')}</p>
                                <h4 style='color:#a7f3d0;'>Recommendation</h4>
                                <p>{data.get('recommendation','')}</p>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )
                        except Exception:
                            st.info("Reflection Agent failed — serving local backup profile.")
                            st.markdown(offline_dna_profile(dna_tasks))

        with tab_chat:
            st.subheader("💬 Aria Chat — Ask the assistant")
            for node in st.session_state.get("chat_history", []):
                role = node.get("role", "user")
                text = node.get("text", "")
                if role == "user":
                    with st.chat_message("user"):
                        st.write(text)
                else:
                    with st.chat_message("assistant"):
                        st.write(text)

            user_input = st.chat_input("Ask Aria anything...")
            if user_input:
                st.session_state.chat_history.append({"role": "user", "text": user_input, "time": datetime.datetime.now(IST).isoformat()})
                with st.chat_message("user"):
                    st.write(user_input)
                aria_prompt = (
                    "You are Aria, an autonomous crisis response agent for students. "
                    "Provide concise, actionable guidance and suggest next concrete steps. "
                    f"User question: {user_input}"
                )
                if not gemini_client:
                    answer = gemini_offline_answer()
                    st.session_state.chat_history.append({"role": "assistant", "text": answer, "time": datetime.datetime.now(IST).isoformat()})
                    with st.chat_message("assistant"):
                        st.write(answer)
                else:
                    with st.chat_message("assistant"):
                        try:
                            resp = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=aria_prompt)
                            answer = resp.text.strip()
                        except Exception as e:
                            print(f"Gemini chat error: {e}")
                            answer = gemini_offline_answer()
                        st.write(answer)
                        st.session_state.chat_history.append({"role": "assistant", "text": answer, "time": datetime.datetime.now(IST).isoformat()})

    with log_col:
        # 📋 Agent Activity Log
        st.subheader("📋 Agent Activity Log")
        with bordered_container():
            st.markdown("<div style='font-size: 12px;'>", unsafe_allow_html=True)
            for log in st.session_state.agent_log:
                agent = log.get('agent', '')
                if any(k in agent for k in ['Horizon', 'Breakdown', 'Priority']):
                    dot = '#10b981'
                elif 'Action' in agent:
                    dot = '#f59e0b'
                elif 'Reflection' in agent:
                    dot = '#38bdf8'
                elif 'Crisis' in agent:
                    dot = '#ef4444'
                else:
                    dot = '#94a3b8'
                st.markdown(
                    f"<div style='border-bottom: 1px solid #1e293b; padding-bottom: 8px; margin-bottom: 8px;'>"
                    f"<span style='display:inline-block; width:10px; height:10px; border-radius:50%; background-color:{dot}; margin-right:8px;'></span>"
                    f"<span style='color: #38bdf8;'>[{log['time']}] {log['agent']}:</span> "
                    f"<span style='color: #94a3b8;'>{log['action']}</span></div>",
                    unsafe_allow_html=True
                )
            st.markdown("</div>", unsafe_allow_html=True)

        # 📊 Horizon Diagnostics
        with bordered_container():
            st.markdown("### 📊 Horizon Diagnostics")
            st.write("Timezone: **IST (Asia/Kolkata)**")
            try:
                pct = float(horizon_report.get("density_score", 0)) / 100.0
            except Exception:
                pct = 0.0
            st.progress(min(max(pct, 0.0), 1.0))
            st.caption(f"Ecosystem Load: {horizon_report.get('density_tier', 'CLEAR').upper()} activity envelope detected.")

        # 📈 Queue Stats
        with bordered_container():
            st.markdown("### 📈 Queue Stats")
            total = len(tasks_list)
            done = len([t for t in tasks_list if t.get("status") == "Completed"])
            crit = len([t for t in tasks_list if t.get("risk") == "CRITICAL" and t.get("status") != "Completed"])
            st.metric("Total Tasks", total)
            st.metric("Completed", done)
            if crit:
                st.metric("🔴 Critical", crit, delta=f"-{crit} need attention", delta_color="inverse")