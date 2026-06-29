"""
ARIA AUTONOMOUS CRON SERVICE
Runs check_proactive_interventions() on a timer independent of browser sessions.
Deploy to: Google Cloud Functions or Cloud Run
Triggered by: Google Cloud Scheduler (HTTP POST)
"""

import os
import json
import datetime
import pytz
from typing import Dict, List, Any
from dotenv import load_dotenv

# Cloud infrastructure imports
import firebase_admin
from firebase_admin import credentials, firestore
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Local agent logic
try:
    from agents import (
        horizon_agent_intelligence_loop,
        check_proactive_interventions,
    )
except ImportError:
    # Fallback if agents module isn't available
    def horizon_agent_intelligence_loop(calendar_events, *, now=None, horizon_hours=48, 
                                       timezone_name="Asia/Kolkata", critical_threshold=70):
        return {"density_tier": "CLEAR", "is_overcrowded": False}
    
    def check_proactive_interventions(tasks_list):
        return []

# =========================================================
# INITIALIZATION
# =========================================================
load_dotenv()

IST = pytz.timezone("Asia/Kolkata")

# Initialize Firebase (Cloud Functions runtime auto-detects credentials)
db = None
try:
    if not firebase_admin.get_app():
        firebase_admin.initialize_app()
    db = firestore.client()
    print("✅ Firebase initialized successfully")
except ValueError:
    # Already initialized
    try:
        db = firestore.client()
        print("✅ Firebase client already available")
    except Exception as e:
        print(f"⚠️ Firebase not available (expected for local testing): {e}")
        db = None
except Exception as e:
    print(f"⚠️ Firebase initialization failed: {e}")
    db = None

# =========================================================
# UTILITIES FOR CRON EXECUTION
# =========================================================

def resolve_user_calendar_credentials(user_id: str) -> Credentials or None:
    """Fetch calendar credentials for a specific user from Firestore."""
    try:
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return None
        
        user_data = user_doc.to_dict()
        google_token = user_data.get("google_token")
        
        if google_token:
            return Credentials.from_authorized_user_info(google_token)
    except Exception as e:
        print(f"[ERROR] Failed to resolve credentials for user {user_id}: {e}")
    
    return None


def fetch_user_calendar_events(user_id: str, horizon_hours: int = 48) -> List[Dict]:
    """Fetch upcoming calendar events for a user."""
    try:
        creds = resolve_user_calendar_credentials(user_id)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                return []
        
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        later = (
            datetime.datetime.now(datetime.timezone.utc) 
            + datetime.timedelta(hours=horizon_hours)
        ).isoformat().replace("+00:00", "Z")
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            timeMax=later,
            maxResults=100,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return events_result.get('items', [])
    
    except HttpError as e:
        print(f"[ERROR] Google Calendar API error for user {user_id}: {e}")
    except Exception as e:
        print(f"[ERROR] Failed to fetch calendar events for {user_id}: {e}")
    
    return []


def fetch_user_tasks(user_id: str) -> List[Dict]:
    """Fetch tasks from Firestore for a specific user."""
    try:
        tasks_ref = db.collection("users").document(user_id).collection("tasks")
        docs = tasks_ref.stream()
        tasks = []
        
        for doc in docs:
            task_data = doc.to_dict()
            task_data['id'] = doc.id
            tasks.append(task_data)
        
        return tasks
    
    except Exception as e:
        print(f"[ERROR] Failed to fetch tasks for {user_id}: {e}")
    
    return []


def run_proactive_check_for_user(user_id: str) -> Dict[str, Any]:
    """
    Execute the full proactive intervention check for a single user.
    Returns a report of alerts and actions taken.
    """
    print(f"\n🔄 [CRON] Running proactive check for user: {user_id}")
    
    if db is None:
        print(f"  → Firebase not available, skipping user check")
        return {"user_id": user_id, "status": "skipped", "reason": "Firebase unavailable"}
    
    try:
        # 1. Fetch user's tasks
        tasks_list = fetch_user_tasks(user_id)
        if not tasks_list:
            print(f"  → No tasks found for {user_id}")
            return {"user_id": user_id, "alerts": [], "status": "no_tasks"}
        
        # 2. Fetch user's calendar to assess density
        calendar_events = fetch_user_calendar_events(user_id, horizon_hours=48)
        
        # 3. Run horizon agent to assess calendar density
        try:
            user_tz = "Asia/Kolkata"  # TODO: fetch from user profile
            now = datetime.datetime.now(IST)
            horizon_report = horizon_agent_intelligence_loop(
                calendar_events,
                now=now,
                horizon_hours=48,
                timezone_name=user_tz,
                critical_threshold=70
            )
        except Exception as e:
            print(f"  → Horizon agent failed: {e}")
            horizon_report = {"density_tier": "CLEAR"}
        
        # 4. Run proactive intervention check
        alerts = check_proactive_interventions(tasks_list)
        
        # 5. Log the action to Firestore
        if db is not None:
            db.collection("user_meta").document(user_id).set(
                {
                    "cron_last_run": datetime.datetime.now(IST).isoformat(),
                    "cron_alert_count": len(alerts),
                    "cron_status": "success",
                },
                merge=True
            )
        
        print(f"  ✅ Check complete: {len(alerts)} alerts generated")
        
        return {
            "user_id": user_id,
            "alerts": alerts,
            "status": "success",
            "alert_count": len(alerts),
        }
    
    except Exception as e:
        print(f"  ❌ Check failed: {e}")
        if db is not None:
            db.collection("user_meta").document(user_id).set(
                {
                    "cron_last_run": datetime.datetime.now(IST).isoformat(),
                    "cron_status": "error",
                    "cron_error": str(e),
                },
                merge=True
            )
        return {"user_id": user_id, "status": "error", "error": str(e)}


def batch_process_all_users() -> Dict[str, Any]:
    """
    Iterate through all users and run proactive intervention checks.
    This is the main entry point for Cloud Scheduler.
    """
    print("\n" + "="*60)
    print("🚀 ARIA AUTONOMOUS CRON EXECUTION STARTED")
    print(f"   Timestamp: {datetime.datetime.now(IST).isoformat()}")
    print("="*60)
    
    results = {
        "execution_timestamp": datetime.datetime.now(IST).isoformat(),
        "total_users_processed": 0,
        "successful_checks": 0,
        "failed_checks": 0,
        "total_alerts_generated": 0,
        "user_results": [],
    }
    
    if db is None:
        print("❌ Firebase not initialized - cannot proceed")
        results["error"] = "Firebase client is not available"
        return results
    
    try:
        # Fetch all user IDs from Firestore
        users_ref = db.collection("users")
        user_docs = users_ref.stream()
        
        for user_doc in user_docs:
            user_id = user_doc.id
            result = run_proactive_check_for_user(user_id)
            
            results["total_users_processed"] += 1
            
            if result.get("status") == "success":
                results["successful_checks"] += 1
                results["total_alerts_generated"] += result.get("alert_count", 0)
            else:
                results["failed_checks"] += 1
            
            results["user_results"].append(result)
    
    except Exception as e:
        print(f"\n❌ BATCH EXECUTION FAILED: {e}")
        results["error"] = str(e)
    
    print("\n" + "="*60)
    print("📊 CRON EXECUTION SUMMARY")
    print(f"   Total Users Processed: {results['total_users_processed']}")
    print(f"   Successful Checks: {results['successful_checks']}")
    print(f"   Failed Checks: {results['failed_checks']}")
    print(f"   Total Alerts Generated: {results['total_alerts_generated']}")
    print("="*60 + "\n")
    
    return results


# =========================================================
# CLOUD FUNCTION ENTRY POINT
# =========================================================

def cron_handler(request) -> tuple:
    """
    HTTP handler for Google Cloud Functions.
    Triggered by Cloud Scheduler with HTTP POST requests.
    
    Expected headers from Cloud Scheduler:
    - Authorization: Bearer [service account token]
    - Content-Type: application/json
    
    Request body can be empty or contain:
    {
        "user_id": "optional_specific_user_id"
    }
    """
    
    # Verify Cloud Scheduler identity (optional but recommended)
    # In production, validate the Authorization header using Google's ID token verification
    
    try:
        request_json = request.get_json(silent=True) or {}
        user_id = request_json.get("user_id")
        
        if user_id:
            # Run check for a specific user
            print(f"🎯 Targeted check requested for user: {user_id}")
            result = run_proactive_check_for_user(user_id)
            return (json.dumps(result), 200, {"Content-Type": "application/json"})
        else:
            # Run batch check for all users
            results = batch_process_all_users()
            return (json.dumps(results), 200, {"Content-Type": "application/json"})
    
    except Exception as e:
        print(f"❌ Handler error: {e}")
        error_response = {
            "error": str(e),
            "timestamp": datetime.datetime.now(IST).isoformat(),
        }
        return (json.dumps(error_response), 500, {"Content-Type": "application/json"})


# =========================================================
# LOCAL TESTING (for development)
# =========================================================

if __name__ == "__main__":
    # Run locally for testing (requires Firebase credentials)
    print("🧪 Running CRON service locally...")
    results = batch_process_all_users()
    print("\n📋 Results:")
    print(json.dumps(results, indent=2))
