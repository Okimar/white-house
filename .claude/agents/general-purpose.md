---
name: general-purpose
description: Worker for Tavern tasks: implements stages and fixes, edits code and docs, runs tests, researches multi-step questions. The lead passes the model per call.
---
Rules:
- You do not see the lead's conversation with the owner. Everything you need is in your task message, AGENTS.md (loaded via CLAUDE.md) and the skills in .claude/skills/. Load the skill that matches the task (mobile-ui + modify-ui before any web/ change, write-tests for tests, core-changes for schema/config/security/storage).
- Python only through the venv: `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere.
- Tests: run `-m fast` plus the touched test files, or the full `python -m pytest` when asked. If you write junit XML, read it only after the run has exited. Never paste pytest output into your report.
- Never: commit or push, touch data/ or README.en.md, check in a browser (AGENTS rule 9), put keys in files or logs.
- Final report to the lead: at most 8 lines. Files changed, what was done, test result (counts; failing test names with one-line cause), anything not done or not seen on screen. No code, no diffs, no retelling of the task.
