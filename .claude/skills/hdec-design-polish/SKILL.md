---
name: hdec-design-polish
description: Use only after HDEC Executive Radar P0-A passes. Applies one limited executive signal desk polish pass to Today Signals only.
---

# HDEC Design Polish

Use only after P0-A functionality passes.

Rules:

1. Polish only `templates/index.html`.
2. Do not add new product scope.
3. Do not redesign the whole app.
4. Keep Today Signals as the only main screen.
5. Improve:
   - spacing
   - card hierarchy
   - score badge
   - alert grade readability
   - executive signal desk feel
6. Avoid:
   - generic admin dashboard look
   - excessive charts
   - animations
   - new dependencies
7. After polish, re-run P0-A verification.
8. If polish breaks Run Sensing → Detail → Send → Feedback, revert the polish.
