import os
from google_auth_oauthlib.flow import InstalledAppFlow

# The exact permission permissions Aria needs
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/drive.file'
]

print("🔄 Initializing absolute force handshake...")

if not os.path.exists('client_secret.json'):
    print("❌ ERROR: Python still can't see client_secret.json in this folder!")
    exit()

try:
    flow = InstalledAppFlow.from_client_secrets_file('client_secret.json', SCOPES)
    print("🚀 Opening your browser window now...")
    creds = flow.run_local_server(port=0)
    
    # Write the token to disk
    with open('token.json', 'w') as token:
        token.write(creds.to_json())
        
    print("✨ SUCCESS! token.json has been created perfectly!")
except Exception as e:
    print(f"❌ Handshake failed: {str(e)}")
