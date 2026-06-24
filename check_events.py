import os
import json
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def resolve_calendar_credentials():
    if os.path.exists('token.json'):
        return Credentials.from_authorized_user_file('token.json')

    if os.environ.get("GOOGLE_TOKEN_JSON"):
        try:
            token_data = json.loads(os.environ.get("GOOGLE_TOKEN_JSON"))
            return Credentials.from_authorized_user_info(token_data)
        except Exception as e:
            print(f"❌ Cloud token parsing failure: {e}")
            return None

    return None

try:
    creds = resolve_calendar_credentials()
    if not creds:
        print("❌ Error: no token.json file or GOOGLE_TOKEN_JSON environment payload was found.")
        exit()

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if not creds.valid:
                print("❌ Error: refreshed Google Calendar credentials are still invalid.")
                exit()
        else:
            print("❌ Error: Google Calendar credentials are invalid and cannot be refreshed.")
            exit()

    service = build('calendar', 'v3', credentials=creds)
    
    # Fetch the next 5 events from now
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    print("🔄 Fetching your upcoming calendar events...")
    
    events_result = service.events().list(calendarId='primary', timeMin=now,
                                              maxResults=5, singleEvents=True,
                                              orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        print("ℹ️ Connection successful, but no upcoming events were found on your Google Calendar.")
    else:
        print("✨ SUCCESS! Your agent can see these events:")
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            print(f"  - {event['summary']} ({start})")
            
except Exception as e:
    print(f"❌ Read failed: {str(e)}")
