@echo off
title MyWhisper
rem Run from the repo folder so .env and whisperer.toml are found.
cd /d "%~dp0"
whisperer %*
if errorlevel 1 (
  echo.
  echo Whisperer exited with an error.
  pause
)
