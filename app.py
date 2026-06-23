import streamlit as st
import os
import json
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
from google import genai
from google.genai import types
from dotenv import load_dotenv

# ==========================================
# 1. INITIALIZATION & CREDENTIALS HANDSHAKE
# ==========================================
load_dotenv()

# Initialize Gemini Client using your modern SDK structure from yesterday
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Initialize Firebase Firestore securely
if not firebase_admin._apps:
    cred = credentials.Certificate("service_account.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Configure Streamlit Dashboard Framework
st.set_page_config(page_title="The Last-Minute Life Saver", page_icon="🚨", layout="wide")
st.title("🚨 The Last-Minute Life Saver — Dev Workspace")

# Secure a persistent Mock User ID state for local hackathon testing
if "user_id" not in st.session_state:
    st.session_state.user_id = "srikar_dev_user"

user_id = st.session_state.user_id

# ==========================================
# 2. CORE AGENT CORE ARCHITECTURE LOGIC
# ==========================================

def breakdown_agent(name: str, desc: str, deadline_days: int, hours: int) -> dict:
    """Refactored 10:00 AM Blueprint: Processes a raw item into nested subtasks."""
    prompt = f"""Break this task into actionable subtasks.
    Task: {name}
    Description: {desc}
    Days until deadline: {deadline_days}
    Estimated total hours: {hours}
    """
    
    system_instruction = """
    You are an expert project decomposition engine. You must analyze the input and output valid JSON only.
    Target JSON format output structure:
    {
      "subtasks": [
        {"name": "Subtask action label", "hours": 1, "day": 1, "priority": "high"}
      ],
      "risk": "low/medium/high",
      "start_today": "First concrete action step"
    }
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"Breakdown compilation boundary exception: {e}")
        return {"subtasks": [], "risk": "medium", "start_today": "Manual review required"}

def priority_agent(tasks: list) -> list:
    """Refactored 2:00 PM Blueprint: Processes array lists through an Eisenhower Matrix ranking pipeline."""
    if not tasks:
        return []
        
    prompt = f"""Rank these tasks using urgency x importance (Eisenhower matrix).
    Today's Date context: {datetime.now().date()}
    Current Open Tasks payload array: {json.dumps(tasks)}
    """
    
    system_instruction = """
    You are an elite productivity scheduler. Prioritize the array items by assigning a score from 1 to 10.
    Return ONLY a raw JSON list formatted exactly like this:
    [{"id": "task_document_id", "priority_score": 9, "reason": "Short summary context step", "do_today": true}]
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.15,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        return []

def log_agent_action(user_id: str, agent: str, action: str, task_id: str = ""):
    """12:00 PM Blueprint: Records historical workflow mutations inside Firestore logs."""
    db.collection("agent_log").document(user_id).collection("entries").add({
        "agent": agent,
        "action": action,
        "task_id": task_id,
        "timestamp": firestore.SERVER_TIMESTAMP
    })

# ==========================================
# 3. INTERACTIVE FRONTEND UI LAYER
# ==========================================

# Sidebar Component panel layout
with st.sidebar:
    st.info(f"🔑 Core Profile Node active: `{user_id}`")

# Form Entry Layout Panel (10:00 AM Task Submission specification)
with st.form("task_creation_form"):
    st.subheader("📝 Queue New Task Payload")
    task_name = st.text_input("Task Label / Scope Target:")
    task_desc = st.text_area("Contextual parameters / descriptions:")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        deadline_days = st.number_input("Days Available Until Target Deadline:", min_value=1, max_value=30, value=3)
    with c2:
        est_hours = st.slider("Total Estimated Human Effort Hours:", min_value=1, max_value=40, value=5)
    with c3:
        category = st.selectbox("Operational Context Category:", ["Academic", "Personal", "Administrative"])
        
    submit_btn = st.form_submit_button("⚡ Instantiate Deconstruction Sequence")

# Process newly submitted tasks
if submit_btn and task_name:
    with st.spinner("Executing Layer 1 Core Deconstruction Agent steps..."):
        # Step A: Run Breakdown Inference Processing
        analysis_payload = breakdown_agent(task_name, task_desc, deadline_days, est_hours)
        
        # Step B: Log the active pipeline invocation event
        log_agent_action(user_id, "Breakdown Agent", f"Deconstructed task input: '{task_name}'")
        
        # Step C: Write the new document entry to Cloud Firestore subcollections
        item_ref = db.collection("tasks").document(user_id).collection("items")
        new_doc_bus = item_ref.add({
            "name": task_name,
            "deadline_days": deadline_days,
            "hours": est_hours,
            "subtasks": analysis_payload.get("subtasks", []),
            "risk": analysis_payload.get("risk", "low"),
            "start_today": analysis_payload.get("start_today", ""),
            "status": "Not Started",
            "priority_score": 5, # Initialize with a default baseline score
            "created_at": firestore.SERVER_TIMESTAMP
        })
        new_task_id = new_doc_bus[1].id
        
    # Step D: Run Layer 2 Multi-Task Priority Evaluation Sorting Sequence
    with st.spinner("Orchestrating Eisenhower Matrix rank index updates..."):
        all_items_snapshot = db.collection("tasks").document(user_id).collection("items").get()
        compile_list = []
        for doc in all_items_snapshot:
            data = doc.to_dict()
            compile_list.append({"id": doc.id, "name": data.get("name"), "deadline_days": data.get("deadline_days")})
            
        rankings = priority_agent(compile_list)
        
        # Write updated priority ranking scores back into database rows
        for rank in rankings:
            t_id = rank.get("id")
            if t_id:
                db.collection("tasks").document(user_id).collection("items").document(t_id).update({
                    "priority_score": rank.get("priority_score", 5)
                })
        
        log_agent_action(user_id, "Priority Orchestrator", "Re-indexed comprehensive database hierarchy matrix positions.")
    st.rerun()

# ==========================================
# 4. DATA RENDERING & HISTORICAL VISUALIZATION
# ==========================================

# Display active tasks stored in database
st.subheader("📋 Your Real-Time Strategic Checklist Matrix")
items_view = db.collection("tasks").document(user_id).collection("items").order_by("priority_score", direction=firestore.Query.DESCENDING).get()

if not items_view:
    st.info("No active tasks currently tracked inside your database workspace node.")
else:
    for doc in items_view:
        task_data = doc.to_dict()
        t_id = doc.id
        score = task_data.get("priority_score", 5)
        
        # Apply the exact color-coded priority border guidelines from your blueprint
        if score >= 8:
            border_color = "#ff4b4b"  # Crimson Alert Red
        elif score >= 5:
            border_color = "#ffa500"  # Warning Indicator Orange
        else:
            border_color = "#00cc66"  # Success Stable Green
            
        with st.container():
            st.markdown(f"""
            <div style="border-left: 6px solid {border_color}; padding: 15px; margin-bottom: 15px; background-color: #1e1e1e; border-radius: 4px;">
                <h4 style="margin:0; color: white;">{task_data.get('name')} <span style="font-size:12px; color:#aaa;">(Priority Weight: {score}/10)</span></h4>
                <p style="margin:5px 0; font-size:14px; color:#ddd;"><strong>Risk Vector:</strong> {task_data.get('risk').upper()} | <strong>Immediate Step:</strong> {task_data.get('start_today')}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Interactive Status dropdown manipulation routing points
            current_status = task_data.get("status", "Not Started")
            status_options = ["Not Started", "In Progress", "Done"]
            
            idx = status_options.index(current_status) if current_status in status_options else 0
            chosen_status = st.selectbox(f"Update State Indicator for {task_data.get('name')}:", status_options, index=idx, key=f"status_{t_id}")
            
            if chosen_status != current_status:
                db.collection("tasks").document(user_id).collection("items").document(t_id).update({"status": chosen_status})
                log_agent_action(user_id, "System UI Router", f"Mutated status for '{task_data.get('name')}' to '{chosen_status}'", task_id=t_id)
                st.rerun()

st.markdown("---")

# Render historical agent activity logs (2:00 PM Blueprint Activity Log Component Specification)
st.subheader("📜 Live Agent Event Stream Logs")
logs_view = db.collection("agent_log").document(user_id).collection("entries").order_by("timestamp", direction=firestore.Query.DESCENDING).limit(20).get()

for log_doc in logs_view:
    l_data = log_doc.to_dict()
    st.caption(f"⏱️ **{l_data.get('agent')}** — {l_data.get('action')}")