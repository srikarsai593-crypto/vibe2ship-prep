#!/bin/bash
# Quick Start: Test Autonomous Cron Service Locally

echo "🚀 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "📝 Starting Flask cron server on http://localhost:8081..."
echo "   Cron endpoint: http://localhost:8081/cron"
echo "   Health check: http://localhost:8081/health"
echo "   Test endpoint: http://localhost:8081/cron/test"
echo ""

python cron_app.py
