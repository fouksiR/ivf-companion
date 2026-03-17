#!/bin/bash
# ══════════════════════════════════════════════════════
# IVF Companion — Setup & Push to GitHub
# Run this from the folder where you downloaded the files
# ══════════════════════════════════════════════════════

# 1. Init git and push
cd "$(dirname "$0")"
git init
git checkout -b main

# 2. Add all files (excluding old drafts)
git add app.py
git add patient-app.html
git add clinician-dashboard.html
git add requirements.txt
git add Dockerfile
git add README.md
git add .gitignore

# 3. Commit
git commit -m "Initial commit — Mira IVF Companion MVP

Phase 1 prototype:
- FastAPI backend (app.py): chat, check-in, screening, escalation
- Patient app (patient-app.html): onboarding, chat, 5-dim check-in, journey
- Clinician dashboard (clinician-dashboard.html): risk table, alerts, drill-down
- 29 granular treatment stages for prospective training data
- PHQ-2/9, GAD-7, FertiQoL screening with conversational delivery
- GREEN/AMBER/RED escalation framework

Co-Authored-By: Claude <noreply@anthropic.com>"

# 4. Push to GitHub
git remote add origin https://github.com/fouksiR/ivf-companion.git
git push -u origin main

echo ""
echo "✅ Done! Check https://github.com/fouksiR/ivf-companion"
