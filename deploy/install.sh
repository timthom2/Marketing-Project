#!/bin/bash
set -e

echo "🚀 Installing TheKey Content Bot..."

# Install dependencies
sudo apt update
sudo apt install -y python3.10 python3.10-venv git curl

# Create user
sudo useradd -m -s /bin/bash thekey || echo "User already exists"

# Setup directories
sudo mkdir -p /home/thekey/thekey-content-bot/logs
sudo chown -R thekey:thekey /home/thekey/thekey-content-bot

# Clone or upload repo (user action required)
echo ""
echo "📂 Repository Setup"
echo "Please upload the repository to /home/thekey/thekey-content-bot"
echo "Example: scp -r Marketing-Project thekey@your-server:/home/thekey/"
echo ""
cd /home/thekey/thekey-content-bot

# Create venv
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Setup server timezone
echo ""
echo "🕐 Setting server timezone to America/Toronto..."
sudo timedatectl set-timezone America/Toronto
timedatectl

# Setup logrotate
sudo cp deploy/thekey-content-bot.logrotate /etc/logrotate.d/thekey-content-bot

# Setup journald limits
sudo sed -i 's/#SystemMaxUse=/SystemMaxUse=200M/' /etc/systemd/journald.conf 2>/dev/null || true
sudo sed -i 's/#MaxRetentionSec=/MaxRetentionSec=30day/' /etc/systemd/journald.conf 2>/dev/null || true
sudo systemctl restart systemd-journald

# Setup .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "⚠️  IMPORTANT: Edit /home/thekey/thekey-content-bot/.env with your credentials:"
    echo "   - OPENAI_API_KEY"
    echo "   - SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS"
    echo "   - EMAIL_FROM, EMAIL_TO"
    echo "   - TZ (optional, server timezone is America/Toronto)"
    echo ""
fi

# Install systemd units
sudo cp deploy/thekey-content-bot.service /etc/systemd/system/
sudo cp deploy/thekey-content-bot.timer /etc/systemd/system/
sudo cp deploy/thekey-review-portal.service /etc/systemd/system/
sudo cp deploy/thekey-review-processor.service /etc/systemd/system/
sudo cp deploy/thekey-review-processor.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable and start timer
sudo systemctl enable thekey-content-bot.timer
sudo systemctl start thekey-content-bot.timer
sudo systemctl enable thekey-review-portal.service
sudo systemctl start thekey-review-portal.service
sudo systemctl enable thekey-review-processor.timer
sudo systemctl start thekey-review-processor.timer

# Setup permissions
sudo chown -R thekey:thekey /home/thekey/thekey-content-bot
sudo chmod 600 /home/thekey/thekey-content-bot/.env

# Show status
echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "1. Edit /home/thekey/thekey-content-bot/.env with your credentials:"
echo "   nano /home/thekey/thekey-content-bot/.env"
echo ""
echo "2. Verify timer status:"
echo "   sudo systemctl list-timers thekey-content-bot"
echo ""
echo "3. Test manual run (this week's test):"
echo "   sudo -u thekey -H bash -c 'cd /home/thekey/thekey-content-bot && source venv/bin/activate && python -m src.run_weekly'"
echo ""
echo "4. View logs:"
echo "   sudo journalctl -u thekey-content-bot -f"
echo "   tail -f /home/thekey/thekey-content-bot/logs/weekly.log"
echo ""
echo "5. Next scheduled run:"
echo "   sudo systemctl show thekey-content-bot.timer --property=NextElapseUSecMonotonic"
echo ""
echo "6. Manual test run command (for immediate testing):"
echo "   sudo -u thekey bash -c 'cd /home/thekey/thekey-content-bot && source venv/bin/activate && python -m src.run_weekly'"
