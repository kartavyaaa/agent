---
description: Run the full quality gate and fix issues before reporting done
---
Run these in order, fixing problems as you go, then report a PASS/FAIL summary per step:
1. ruff check . --fix
2. black .
3. mypy .
4. pytest
If anything fails, fix it and re-run. Do not report PASS unless every step passes. If a step
can't run for an environmental reason, say so explicitly instead of skipping silently.