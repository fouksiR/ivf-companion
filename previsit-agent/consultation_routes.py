from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List
import pathlib, time

router = APIRouter()

# These get monkey-patched from app.py's existing helpers
_session_get = None
_session_update = None

def init_helpers(get_fn, update_fn):
    global _session_get, _session_update
    _session_get = get_fn
    _session_update = update_fn

def score_profile(answers):
    q1,q2,q3,q4,q5,q6,q7 = answers
    p = [
        {"type":"Driver","score":q1+q4+q6-q2-q3,"description":"High activation and autonomy. Often best with concise options, efficient discussion, and shared decision-making."},
        {"type":"Support-Seeker","score":q2+q3+(6-q1),"description":"May value reassurance, clear structure, and steady guidance. Usually benefits from warmth, clarity, and predictable next steps."},
        {"type":"Avoider","score":(6-q1)+(6-q6)+q7+q3,"description":"May feel overloaded or hesitant. Often best supported with simplified plans, reduced friction, and one clear step at a time."},
        {"type":"Skeptic","score":q5+q4+max(0,q7-2),"description":"Often prefers evidence, transparency, and time to evaluate options. Usually responds well to rationale, data, and room for questions."},
    ]
    p.sort(key=lambda x: x["score"], reverse=True)
    return p[0]

@router.get("/form/{token}", response_class=HTMLResponse)
async def serve_form(token: str):
    session = _session_get(token)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("consultation_completed"):
        return HTMLResponse("<h2 style='text-align:center;margin-top:40vh;font-family:sans-serif;color:#555'>Thank you — your questionnaire has already been submitted.</h2>")
    html = pathlib.Path("static/consultation.html").read_text()
    html = html.replace("{{SESSION_TOKEN}}", token)
    return HTMLResponse(html)

class ConsultationSubmission(BaseModel):
    answers: List[int]

@router.post("/api/submit/{token}")
async def submit_consultation(token: str, body: ConsultationSubmission):
    session = _session_get(token)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("consultation_completed"):
        raise HTTPException(400, "Already submitted")
    if len(body.answers) != 7 or not all(1 <= a <= 5 for a in body.answers):
        raise HTTPException(422, "Expected 7 answers, each 1-5")
    profile = score_profile(body.answers)
    _session_update(token, {
        "consultation_completed": True,
        "consultation_submitted_at": time.strftime("%b %d, %Y %I:%M %p"),
        "consultation_answers": body.answers,
        "consultation_profile": {"type": profile["type"], "score": profile["score"], "description": profile["description"]},
        "status": "COMPLETED",
    })
    return {"ok": True, "profile": profile["type"]}
