#!/bin/bash

# Setup script for the ppchange cron jobs. Run this manually on the server if
# needed. The GitHub Actions deploy workflow installs the same schedule.

DEPLOY_PATH="/opt/ppchange_api"
API_LOG="/var/log/ppchange_api.log"
AUTO_LOG="/var/log/ppchange_auto.log"

# Create log files if they don't exist
for LOG_FILE in "$API_LOG" "$AUTO_LOG"; do
    if [ ! -f "$LOG_FILE" ]; then
        sudo touch "$LOG_FILE"
        sudo chown $USER:$USER "$LOG_FILE"
        echo "Created log file: $LOG_FILE"
    fi
done

# Both jobs run hourly. Use full path to venv python - avoids 'source' which
# doesn't work in cron's /bin/sh.
CRON_API="0 * * * * cd $DEPLOY_PATH && $DEPLOY_PATH/venv/bin/python api_transactions.py >> $API_LOG 2>&1"
CRON_AUTO="15 * * * * cd $DEPLOY_PATH && $DEPLOY_PATH/venv/bin/python sync_auto.py >> $AUTO_LOG 2>&1"

# Remove existing entries by SCRIPT NAME (not "ppchange_api" - that string is in
# both paths and would wipe the sync_auto line too), then add both.
(crontab -l 2>/dev/null | grep -v "api_transactions.py" | grep -v "sync_auto.py" ; \
 echo "$CRON_API" ; echo "$CRON_AUTO") | crontab -

echo "Cron jobs installed successfully!"
echo "Schedule: every hour (api_transactions at :00, sync_auto at :15)"
echo "Logs: $API_LOG , $AUTO_LOG"
echo ""
echo "To verify, run: crontab -l"
