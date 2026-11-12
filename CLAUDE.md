# CLAUDE.md

Project instructions live in AGENTS.md (shared by all agents). Read it in full before working.

@AGENTS.md

Claude Code specifics:
- Skills are in `.claude/skills/`; load the one that matches the task.
- Run Python through the venv: `.venv/Scripts/python.exe` on Windows, `.venv/bin/python` elsewhere.
- Sub-agents are in `.claude/agents/`: `scout` and `Explore` (haiku, read-only search, short answers), `general-purpose` (the worker; model chosen per call, report ≤ 8 lines). Unused ecc plugin agents are hidden in `.claude/settings.json`.
