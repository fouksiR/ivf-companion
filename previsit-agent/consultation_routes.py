"""
Consultation questionnaire routes — add to your existing FastAPI app.

Endpoints:
  GET  /form/{token}   → serves the consultation HTML
  POST /api/submit/{token} → receives answers, scores profile, writes to RTDB
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import List
import httpx, os, time, math

router = APIRouter()

# ── Firebase RTDB config ──────────────────────────────────────────
RTDB_BASE = "https://fertility-gp-portal-default-rtdb.asia-southeast1.firebasedatabase.app"
RTDB_PATH = "previsit/sessions"


async def rtdb_get(path: str):
    """GET from RTDB."""
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{RTDB_BASE}/{path}.json")
        r.raise_for_status()
        return r.json()


async def rtdb_patch(path: str, data: dict):
    """PATCH (merge) into RTDB."""
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{RTDB_BASE}/{path}.json", json=data)
        r.raise_for_status()
        return r.json()


# ── Scoring logic (mirrors the JS version) ────────────────────────
def score_profile(answers: List[int]) -> dict:
    q1, q2, q3, q4, q5, q6, q7 = answers
    profiles = [
        {
            "type": "Driver",
            "score": q1 + q4 + q6 - q2 - q3,
            "description": "High activation and autonomy. Often best with concise options, efficient discussion, and shared decision-making.",
        },
        {
            "type": "Support-Seeker",
            "score": q2 + q3 + (6 - q1),
            "description": "May value reassurance, clear structure, and steady guidance. Usually benefits from warmth, clarity, and predictable next steps.",
        },
        {
            "type": "Avoider",
            "score": (6 - q1) + (6 - q6) + q7 + q3,
            "description": "May feel overloaded or hesitant. Often best supported with simplified plans, reduced friction, and one clear step at a time.",
        },
        {
            "type": "Skeptic",
            "score": q5 + q4 + max(0, q7 - 2),
            "description": "Often prefers evidence, transparency, and time to evaluate options. Usually responds well to rationale, data, and room for questions.",
        },
    ]
    profiles.sort(key=lambda p: p["score"], reverse=True)
    return profiles[0]


# ── Routes ─────────────────────────────────────────────────────────

@router.get("/form/{token}", response_class=HTMLResponse)
async def serve_form(token: str):
    """Serve the consultation questionnaire for a valid session."""
    session = await rtdb_get(f"{RTDB_PATH}/{token}")
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("consultation_completed"):
        return HTMLResponse("<h2 style='text-align:center;margin-top:40vh;font-family:sans-serif;color:#555'>Thank you — your questionnaire has already been submitted.</h2>")

    # Read the HTML template and inject the token
    import pathlib
    html = pathlib.Path("static/consultation.html").read_text()
    html = html.replace("{{SESSION_TOKEN}}", token)
    return HTMLResponse(html)


class ConsultationSubmission(BaseModel):
    answers: List[int]  # 7 values, each 1-5


@router.post("/api/submit/{token}")
async def submit_consultation(token: str, body: ConsultationSubmission):
    """Receive answers, compute profile, store in RTDB."""
    session = await rtdb_get(f"{RTDB_PATH}/{token}")
    if not session:
        raise HTTPException(404, "Session not found")
    if session.get("consultation_completed"):
        raise HTTPException(400, "Already submitted")

    if len(body.answers) != 7 or not all(1 <= a <= 5 for a in body.answers):
        raise HTTPException(422, "Expected 7 answers, each 1-5")

    profile = score_profile(body.answers)

    await rtdb_patch(f"{RTDB_PATH}/{token}", {
        "consultation_completed": True,
        "consultation_submitted_at": time.strftime("%b %d, %Y %I:%M %p"),
        "consultation_answers": body.answers,
        "consultation_profile": {
            "type": profile["type"],
            "score": profile["score"],
            "description": profile["description"],
        },
        "status": "COMPLETED",
    })

    return {"ok": True, "profile": profile["type"]}


# ── Wire into your existing app ───────────────────────────────────
# In your main app.py, add:
#
#   from consultation_routes import router as consultation_router
#   app.include_router(consultation_router)
