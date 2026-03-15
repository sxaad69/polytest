#!/bin/bash
# Pull latest from git and restart the bot service.
# Run from /home/ubuntu/polytest on your AWS instance:
#   ./deploy.sh

set -e

SERVICE_NAME="polymarket-bot"

echo ""
echo "→ Pulling latest from git..."
git pull

echo "→ Installing any new dependencies..."
source .venv/bin/activate
pip install -r requirements.txt --quiet

echo "→ Restarting bot service..."
sudo systemctl restart $SERVICE_NAME
sleep 2

echo "→ Status:"
sudo systemctl status $SERVICE_NAME --no-pager -l

echo ""
echo "→ Live logs (Ctrl+C to stop watching):"
sudo journalctl -u $SERVICE_NAME -f
