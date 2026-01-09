# TheKey Content Bot - Deployment Guide

## Overview
This guide covers deploying the TheKey Canada SEO Content Bot to a Linode server (Ubuntu) via systemd timer.

## Prerequisites
- Linode server with Ubuntu 20.04+ or 22.04+
- Sudo access
- OpenAI API key
- SMTP credentials (Postmark recommended)
- Git access or ability to upload files

## Quick Start Installation

### 1. Upload Repository
```bash
# Option A: Git clone (if repository is public)
sudo -u thekey git clone <repo-url> /home/thekey/thekey-content-bot

# Option B: Upload via SCP (from your local machine)
scp -r Marketing-Project thekey@your-linode-ip:/home/thekey/
```

### 2. Run Install Script
```bash
cd /home/thekey/thekey-content-bot
sudo bash deploy/install.sh
```

### 3. Set Server Timezone
```bash
# Set system timezone to America/Toronto
sudo timedatectl set-timezone America/Toronto

# Verify timezone
timedatectl
```

### 4. Configure Environment Variables
```bash
sudo -u thekey nano /home/thekey/thekey-content-bot/.env
```

Add your credentials:
```bash
# OpenAI API
OPENAI_API_KEY=sk-proj-...

# Email (Postmark example)
SMTP_HOST=smtp.postmarkapp.com
SMTP_PORT=587
SMTP_USER=your-postmark-token
SMTP_PASS=your-postmark-token
SMTP_USE_TLS=false
SMTP_STARTTLS=true
EMAIL_FROM=content-bot@thekey.com
EMAIL_TO=tt@thekey.com

# Review portal (optional)
REVIEW_MODE=off
REVIEW_PORTAL_BASE_URL=https://review.your-domain.com
REVIEW_DEADLINE_HOURS=48
REVIEW_REMINDER_HOURS=24
REVIEW_REPLY_TO=content-review@your-domain.com
REVIEW_FINAL_EMAIL_TO=content-ops@your-domain.com
REVIEW_PROCESS_ON_SUBMIT=true

# Timezone (optional, service uses system timezone)
TZ=America/Toronto
```

### 5. Test Manual Run
```bash
sudo -u thekey bash -c 'cd /home/thekey/thekey-content-bot && source venv/bin/activate && python -m src.run_weekly'
```

### 6. Verify Timer
```bash
# Check timer status
sudo systemctl list-timers thekey-content-bot

# Check next scheduled run
sudo systemctl show thekey-content-bot.timer --property=NextElapseUSecMonotonic

# View timer logs
sudo journalctl -u thekey-content-bot -f
```

## Manual Test Run

To run the bot immediately (without waiting for scheduled time):

```bash
sudo -u thekey bash -c 'cd /home/thekey/thekey-content-bot && source venv/bin/activate && python -m src.run_weekly'
```

## Viewing Logs

### Systemd Journal
```bash
# Follow logs in real-time
sudo journalctl -u thekey-content-bot -f

# View last 100 lines
sudo journalctl -u thekey-content-bot -n 100

# View logs since last boot
sudo journalctl -u thekey-content-bot --since today
```

### Application Logs
```bash
# Follow log file
tail -f /home/thekey/thekey-content-bot/logs/weekly.log

# View last 50 lines
tail -n 50 /home/thekey/thekey-content-bot/logs/weekly.log
```

## Troubleshooting

### Email Not Sending
1. Verify SMTP credentials in `.env`
2. Check firewall: `sudo ufw status`
3. Test SMTP manually (use telnet or openssl)
4. Check logs: `sudo journalctl -u thekey-content-bot -n 100`

### Timer Not Triggering
1. Check timer is enabled: `sudo systemctl is-enabled thekey-content-bot.timer`
2. Check timer status: `sudo systemctl list-timers`
3. Reload systemd: `sudo systemctl daemon-reload`
4. Restart timer: `sudo systemctl restart thekey-content-bot.timer`

### OpenAI API Errors
1. Verify `OPENAI_API_KEY` is set correctly
2. Check API quota: https://platform.openai.com/usage
3. Check network connectivity

### Permission Errors
```bash
# Fix permissions
sudo chown -R thekey:thekey /home/thekey/thekey-content-bot
sudo chmod 600 /home/thekey/thekey-content-bot/.env
```

## Systemd Service Management

### Start/Stop/Restart
```bash
# Start service manually (not timer)
sudo systemctl start thekey-content-bot.service

# Stop service
sudo systemctl stop thekey-content-bot.service

# Restart service
sudo systemctl restart thekey-content-bot.service

# Disable timer (stop scheduled runs)
sudo systemctl disable thekey-content-bot.timer

# Enable timer (start scheduled runs)
sudo systemctl enable thekey-content-bot.timer
```

### Review Portal Services
```bash
# Start review portal
sudo systemctl start thekey-review-portal.service

# Check review portal status
sudo systemctl status thekey-review-portal.service

# Check review processor timer
sudo systemctl list-timers thekey-review-processor.timer
```

### Reload Configuration
```bash
# After changing .env or code
sudo systemctl daemon-reload
sudo systemctl restart thekey-content-bot.timer
```

## DNS Notes

If you want to access the bot's output directory via web:

1. Configure web server (Apache/Nginx) to serve `/home/thekey/thekey-content-bot/outputs`
2. Set appropriate permissions: `sudo chmod 755 /home/thekey/thekey-content-bot/outputs`
3. Configure DNS (optional, for web access)

## Security Recommendations

1. **SSH Access**: Use key-based authentication, disable password auth
2. **Firewall**: Enable UFW, allow only necessary ports (SSH: 22)
3. **Updates**: Run `sudo apt update && sudo apt upgrade` regularly
4. **Logs**: Monitor logs for suspicious activity
5. **Backups**: Backup outputs directory regularly

## Monitoring

### Check Disk Space
```bash
df -h /home/thekey/thekey-content-bot
```

### Check Log Rotation
```bash
sudo logrotate -f /etc/logrotate.d/thekey-content-bot --verbose
```

### Check Token Usage
Tokens are tracked in application logs. Review weekly for cost monitoring.

## Updating the Bot

```bash
cd /home/thekey/thekey-content-bot
source venv/bin/activate
git pull  # or upload new files
pip install -r requirements.txt --upgrade
sudo systemctl daemon-reload
sudo systemctl restart thekey-content-bot.timer
```

## Output Directory Structure

```
/home/thekey/thekey-content-bot/outputs/
├── 2026-01-06/
│   ├── run_summary.json
│   ├── editor_report.json
│   ├── uniqueness_report.json
│   ├── montreal.html
│   ├── montreal.json
│   ├── toronto.html
│   ├── toronto.json
│   ... (8 markets total)
└── 2026-01-13/
    └── ...
```

Older runs (>12 months) are automatically archived as `.tar.gz` files.

## Support

For issues or questions:
1. Check logs: `sudo journalctl -u thekey-content-bot -n 100`
2. Check application logs: `tail -f /home/thekey/thekey-content-bot/logs/weekly.log`
3. Review troubleshooting section above
4. Contact development team
