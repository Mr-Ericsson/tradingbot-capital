@echo off
REM Auto-close positions daily script
cd /d "C:\Users\marcu\OneDrive\ChatGPT\Daytrading_Capital\tradingbot"
python auto_close_positions.py --account-mode demo --close-offset 30 >> logs\auto_close_daily.log 2>&1