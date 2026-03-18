# Melod-AI: Signal Integration & Human Escalation — Integration Guide

## What's in this package

| File | Purpose |
|------|---------|
| `signal_integration.py` | Backend module: signal endpoints + analysis + chat context injection |
| `clinician-dashboard.html` | Complete rebuilt clinician dashboard (Melod-AI design) |
| `human-widget-patch.js` | Patient app escalation widget (CSS + HTML + JS to merge into index.html) |

---

## 1. Backend Speed Problem

The backend is slow because **every chat message** likely goes through multiple Claude API calls 
(triage → response generation → safety check). Two fixes:

### Quick fix: Skip triage for simple messages
In `app.py`, in your `/chat` endpoint, before calling Claude:

```python
# If message is short and conversational, skip triage — go straight to Sonnet
if len(message.strip().split()) < 15 and not any(w in message.lower() for w in ['hurt', 'crisis', 'suicide', 'harm', 'emergency']):
    # Skip Haiku triage, use default tier
    tier = 2  
else:
    tier = await triage_message(message)  # existing triage call
```

### Better fix: Stream responses
If not already streaming, switch `/chat` to SSE so the patient sees tokens arriving immediately:

```python
@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    async def generate():
        # ... build prompt with signal context ...
        async with anthropic_client.messages.stream(...) as stream:
            async for text in stream.text_stream:
                yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## 2. Wiring Signal Analysis into Backend

### Step 1: Add signal_integration.py to the repo
Copy `signal_integration.py` into your repo root (next to `app.py`).

### Step 2: Wire into app.py
Add these lines near the top of `app.py`:

```python
from signal_integration import (
    signal_router,
    get_signal_context_for_patient,
    patient_signal_store
)

# After creating `app = FastAPI(...)`
app.include_router(signal_router)
```

### Step 3: Inject signal context into chat
In your `/chat` endpoint, before calling Claude, add:

```python
# Get passive signal assessment for this patient
signal_ctx = get_signal_context_for_patient(patient_id)

# Append to system prompt
system_prompt = MELOD_AI_SYSTEM_PROMPT + signal_ctx
```

This means Claude's responses will now be informed by:
- Check-in scores (mood, anxiety, hope, etc.)
- Passive signal constructs (psychomotor changes, sleep disturbance, etc.)
- Escalation level (GREEN/AMBER/RED)
- Specific instructions for how to adapt tone

### Step 4: Return escalation level in chat response
In the chat response JSON, add the escalation level:

```python
return {
    "response": ai_response,
    "escalation_level": patient_signal_store.get(patient_id, {}).get("escalation_level", "GREEN"),
    # ... other fields
}
```

---

## 3. Wiring the Patient App (index.html)

### Step 1: Add human widget HTML + CSS + JS
Open `human-widget-patch.js` and follow the three sections:
1. Copy the CSS into your `<style>` block
2. Copy the HTML `<div>` before `</body>`
3. Copy the JavaScript into your `<script>` block

### Step 2: Wire check-in submission
Find your existing check-in submit handler and add:

```javascript
const result = await res.json();
checkAndShowHumanWidget(result);  // ← add this line
```

### Step 3: Wire chat response
After receiving AI response:

```javascript
checkEscalationFromChat(result);  // ← add this line
```

### Step 4: Wire passive collector flush
Update your `PassiveCollector.flush()` to:
- POST to `/signals` (not just store locally)
- Check the response for `escalation_level === 'RED'`
- If RED, call `showHumanWidget()`

---

## 4. Deploying the Clinician Dashboard

### Replace clinician-dashboard.html in the repo
```bash
cp clinician-dashboard.html /path/to/repo/clinician-dashboard.html
git add clinician-dashboard.html
git commit -m "Rebuild clinician dashboard — Melod-AI design + real-time signals"
git push
```

It will be live at: `https://fouksir.github.io/ivf-companion/clinician-dashboard.html`

### How it works
- Polls `/clinician/patients` and `/clinician/alerts` every 8 seconds
- Patients sorted by urgency: human escalation → RED → AMBER → GREEN
- Alert banner pulses when a patient requests human support
- Click any patient card to see detailed signal history + check-in trends
- Acknowledge alerts to clear them

---

## 5. The Full Data Flow

```
Patient App (index.html)
    │
    ├── Every 60s: POST /signals (passive phenotyping data)
    │       → signal_integration.py runs 7 construct detectors
    │       → Returns escalation_level (may trigger human widget)
    │
    ├── Check-in: POST /checkin (mood, anxiety, hope, etc.)
    │       → Updates check-in history
    │       → Re-runs assessment with updated trends
    │       → Returns show_human_widget: true if RED
    │
    ├── Chat: POST /chat 
    │       → System prompt now includes signal assessment context
    │       → Claude adapts tone based on GREEN/AMBER/RED
    │       → Response includes escalation_level
    │
    └── Escalation: POST /escalate/human (patient pressed "talk to someone")
            → Adds URGENT alert to clinician alert queue
            → Sets human_escalation_requested flag
            │
            ▼
Clinician Dashboard (clinician-dashboard.html)
    │
    ├── Polls GET /clinician/patients (every 8s)
    │       → Shows all patients sorted by urgency
    │       → Active constructs, check-in scores, signal summary
    │
    ├── Polls GET /clinician/alerts (every 8s)
    │       → Shows alert feed with human escalation at top
    │       → Pulsing banner for active human requests
    │
    └── GET /clinician/patient/{id} (on click)
            → Detailed view: signal history, check-in chart, constructs
```

---

## 6. Deploy to Cloud Run

After integrating:

```bash
cd /path/to/ivf-companion
git add signal_integration.py clinician-dashboard.html index.html
git commit -m "Wire signal analysis + human escalation + new clinician dashboard"
git push

# Deploy backend
gcloud run deploy ivf-companion \
    --source . \
    --region australia-southeast1 \
    --project fertility-gp-portal \
    --allow-unauthenticated \
    --set-env-vars ANTHROPIC_API_KEY=sk-ant-YOUR-REAL-KEY \
    --memory 2Gi
```

Frontend auto-deploys via GitHub Pages push.

---

## Known Limitations (address later)

- **In-memory storage**: All patient data lost on Cloud Run restart. Priority: add PostgreSQL.
- **No auth**: Anyone can hit clinician endpoints. Add API key or session auth.
- **Polling not WebSockets**: 8s delay on clinician dashboard. Fine for now.
- **Population baselines**: Hardcoded norms used for first 7 sessions. Should be calibrated with real data.
