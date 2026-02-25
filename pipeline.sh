#!/bin/bash
# Run this manually in the Railway shell to seed the database
# Or it runs automatically via the cron service daily
set -e
echo "=== Starting pipeline ==="
python monitor.py fetch --since-hours 72
python monitor.py analyze --max-episodes 150
echo "=== Pipeline complete ==="
