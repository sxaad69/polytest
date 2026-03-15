#!/bin/bash
# First-time setup on a fresh EC2 instance (Ubuntu 22.04+)
# Run once after cloning the repo:
#   bash setup_aws.sh

set -e

PROJECT_DIR="/home/ubuntu/polymarket"
SERVICE_NAME="polymarket-bot"

echo ""
echo "=== Polymarket Bot — AWS Setup ==="
echo ""

# 1. System packages
echo "→ Installing system dependencies..."
sudo apt-get update -q
sudo apt-get install -y python3 python3-pip python3-venv git

# 2. Python venv
echo "→ Creating virtual environment..."
cd $PROJECT_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo "  Dependencies installed"

# 3. Create data directory
mkdir -p data
echo "  data/ directory ready"

# 4. .env file
if [ ! -f .env ]; then
    echo ""
    echo "→ Creating .env file..."
    echo "  Paste your Alchemy RPC URL and press Enter:"
    read -p "  ALCHEMY_RPC_URL=" ALCHEMY_URL
    echo "ALCHEMY_RPC_URL=$ALCHEMY_URL" > .env
    echo "  .env created"
else
    echo "→ .env already exists — skipping"
fi

# 5. Run health check
echo ""
echo "→ Running health check..."
python test_bot.py
echo ""

# 6. Make scripts executable
chmod +x deploy.sh
echo "→ deploy.sh is now executable"

# 7. Install systemd service
echo "→ Installing systemd service..."
sudo cp $SERVICE_NAME.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
echo "  Service installed and enabled"

# 8. Start
echo "→ Starting bot..."
sudo systemctl start $SERVICE_NAME
sleep 2
sudo systemctl status $SERVICE_NAME --no-pager -l

echo ""
echo "=== Setup complete ==="
echo ""
echo "Useful commands:"
echo "  sudo journalctl -u $SERVICE_NAME -f          → live logs"
echo "  sudo journalctl -u $SERVICE_NAME --since today → today's logs"
echo "  sudo systemctl status $SERVICE_NAME           → is it running?"
echo "  sudo systemctl stop $SERVICE_NAME             → stop"
echo "  sudo systemctl restart $SERVICE_NAME          → restart"
echo "  ./deploy.sh                                    → pull latest + restart"
echo ""
