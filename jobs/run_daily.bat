@echo off
rem 每日排程入口：由 Windows 工作排程器呼叫（見 jobs\register_task.ps1）
cd /d D:\Project\Revenue
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m jobs.daily >> logs\scheduler.log 2>&1
exit /b %ERRORLEVEL%
