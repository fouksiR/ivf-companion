# Melod-AI — Repo Cleanup & Deployment Checklist

## File Status

### KEEP (active files)
| File | Status | Notes |
|------|--------|-------|
| `app.py` | **PATCH NEEDED** | Run `python patch_backend.py` to rename Mira → Melod + wire signal analysis |
| `signal_analysis.py` | **NEW** | Backend passive signal analysis engine — must be in same dir as app.py |
| `patient-app.html` | **UPDATED** | Melod-AI frontend with passive collector + API_BASE connected |
| `clinician-dashboard.html` | **RENAME NEEDED** | Still says "Mira" — update title + brand to Melod-AI |
| `passive-collector.js` | **REFERENCE ONLY** | Documented standalone version — actual code is embedded in patient-app.html |
| `patch_backend.py` | **RUN ONCE** | Patches app.py, creates backup. Delete after running. |
| `build_vectorstore.py` | KEEP | Education RAG builder |
| `knowledge/` | KEEP | Education content for RAG |
| `Dockerfile` | KEEP | No changes needed |
| `requirements.txt` | KEEP | No changes needed |
| `SETUP.sh` | KEEP | No changes needed |
| `README.md` | **UPDATE NEEDED** | Rename Mira → Melod-AI throughout |

### DELETE (legacy / unused)
| File | Why |
|------|-----|
| `index.html` | Old draft frontend — replaced by patient-app.html |
| `dashboard.html` | Old draft dashboard — replaced by clinician-dashboard.html |
| `IVF_Companion_Architecture_Spec.md` | Outdated spec — superseded by Developer Recipe |
| `Mira_Passive_Data_Collection_Spec.docx` | Original spec — now implemented in signal_analysis.py |

## Deployment Steps

### 1. Patch the backend
```bash
cd ivf-companion
python patch_backend.py
# This creates app.py.bak (backup) and patches app.py in-place
```

### 2. Verify signal_analysis.py is present
```bash
ls signal_analysis.py  # Must exist alongside app.py
```

### 3. Test locally
```bash
export ANTHROPIC_API_KEY=sk-ant-your-key
pip install fastapi uvicorn anthropic pydantic
uvicorn app:app --reload --port 8080

# Then open patient-app.html in a browser
# Temporarily change API_BASE to 'http://localhost:8080' for local testing
```

### 4. Deploy to Cloud Run
```bash
gcloud run deploy ivf-companion \
  --source . \
  --region australia-southeast1 \
  --allow-unauthenticated \
  --set-env-vars ANTHROPIC_API_KEY=sk-ant-your-key \
  --memory 2Gi
```

### 5. Verify the connection
Open patient-app.html → complete onboarding → send a message.
- If you see a real AI response (not the fallback text), the frontend → backend connection is live.
- Open browser DevTools → Console. You should see `[Phenotyping] Init xxxx-xxxx` and periodic `[Phenotyping] Flushed N signals` messages.

### 6. Clean up repo
```bash
git rm index.html dashboard.html
git rm IVF_Companion_Architecture_Spec.md
git rm Mira_Passive_Data_Collection_Spec.docx
git rm patch_backend.py  # after running it
git commit -m "Clean up legacy files, rename to Melod-AI"
```

## Connection Map (after patching)

```
patient-app.html
  └── API_BASE = 'https://ivf-companion-532857641879.australia-southeast1.run.app'
       │
       ├── POST /onboard          → app.py → Claude Sonnet → welcome message
       ├── POST /chat             → app.py → Haiku triage → Haiku safety → Sonnet response
       ├── POST /checkin          → app.py → store + Sonnet response
       ├── POST /passive-signals  → app.py → signal_analysis.py → risk assessment → escalation_db
       └── PATCH /patient         → app.py → update stage/details

clinician-dashboard.html
  └── API_BASE = same URL
       ├── GET /clinician/dashboard       → patient cohort + risk levels
       └── GET /clinician/patient/{id}    → detail + AI summary
```

## What the passive collector actually sends

Every 60 seconds (and on app background), the frontend flushes to `/passive-signals`:

```json
{
  "patient_id": "abc123",
  "session_id": "a1b2-c3d4",
  "signals": [
    {"signal_type": "message_sent", "value": 47, "timestamp": "...", "metadata": {"word_count": 8, "negative_word_count": 2, ...}},
    {"signal_type": "tab_switch", "value": null, "metadata": {"from": "chat", "to": "checkin"}},
    {"signal_type": "checkin_complete", "value": 45000, "metadata": {"scores": {"mood": 3, ...}}}
  ],
  "derived_features": {
    "typing_speed_mean_ms": 142,
    "deletion_ratio": 0.23,
    "message_length_mean": 38,
    "touch_velocity_mean": 0.8,
    "scroll_direction_changes": 12,
    "session_duration_ms": 180000,
    "is_late_night": false,
    "navigation_entropy": 1.4,
    "battery_level": 67
  },
  "session_metadata": {
    "start": "2026-03-18T20:00:00Z",
    "duration_ms": 180000,
    "hour_of_day": 20,
    "timezone": "Australia/Melbourne"
  }
}
```

The backend then runs `signal_analysis.process_passive_signals()` which:
1. Adds session to patient's personal baseline (calibrates after 7 sessions)
2. Computes z-scores against baseline for all derived features
3. Evaluates 7 clinical constructs (psychomotor, sleep, withdrawal, rumination, anhedonia, anxiety, hopelessness)
4. Produces composite risk score → maps to GREEN/AMBER/RED
5. If AMBER/RED → feeds into existing escalation_db → shows on clinician dashboard
