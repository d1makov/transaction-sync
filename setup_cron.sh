#!/bin/bash

# Setup script for ppchange_api cron job
# Run this manually on the server if needed

DEPLOY_PATH="/opt/ppchange_api"
LOG_FILE="/var/log/ppchange_api.log"

# Create log file if it doesn't exist
if [ ! -f "$LOG_FILE" ]; then
    sudo touch "$LOG_FILE"
    sudo chown $USER:$USER "$LOG_FILE"
    echo "Created log file: $LOG_FILE"
fi

# Define the cron job (runs every 6 hours)
# Use full path to venv python - avoids 'source' which doesn't work in cron's /bin/sh
CRON_CMD="0 */6 * * * cd $DEPLOY_PATH && $DEPLOY_PATH/venv/bin/python api_transactions.py >> $LOG_FILE 2>&1"

# Remove existing ppchange_api cron entries and add the new one
(crontab -l 2>/dev/null | grep -v "ppchange_api" ; echo "$CRON_CMD") | crontab -

echo "Cron job installed successfully!"
echo "Schedule: Every 6 hours (at minute 0)"
echo "Log file: $LOG_FILE"
echo ""
echo "To verify, run: crontab -l"
echo "To view logs, run: tail -f $LOG_FILE"
