"""
ARIA CRON SERVICE - STANDALONE FLASK APP
For local testing or deployment to Cloud Run alongside Streamlit.

Run locally: python cron_app.py
This starts a Flask server on port 8081 (can be called by Cloud Scheduler).
"""

from flask import Flask, request, jsonify
import os
from dotenv import load_dotenv

# Import the cron handler from cron_service
from cron_service import cron_handler as _cron_handler, batch_process_all_users

load_dotenv()

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Cloud Run."""
    return jsonify({"status": "healthy"}), 200


@app.route('/cron', methods=['POST'])
def cron_endpoint():
    """
    Main cron endpoint for Cloud Scheduler.
    
    Accepts POST requests from Google Cloud Scheduler.
    Optional JSON body: {"user_id": "user123"} for targeted checks.
    """
    # Call the Cloud Function handler
    response, status_code, headers = _cron_handler(request)
    return app.response_class(response=response, status=status_code, headers=headers)


@app.route('/cron/test', methods=['GET'])
def test_cron():
    """Simple test endpoint (no authentication required)."""
    try:
        results = batch_process_all_users()
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Local development
    port = int(os.getenv('PORT', 8081))
    app.run(host='0.0.0.0', port=port, debug=False)
