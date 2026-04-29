"""
Intake Bridge — Async client for the fouks-intake pre-clinic form service.
==========================================================================
Plumbing only. No business logic. No clinical content. No fallback rendering.
Mirrors the role of fertool_bridge.py but for the lead/pre-visit-form service.

All requests authenticate via the X-Bridge-Secret header. Reads INTAKE_URL
and BRIDGE_SECRET from environment.

Public coroutines:
    create_lead(payload) -> dict
    list_leads(status=None, since=None, limit=100) -> list[dict]
    get_lead_detail(patient_id) -> dict
    mark_converted(patient_id, firebase_uid) -> dict
    delete_lead(patient_id) -> None

On non-2xx responses the helpers raise IntakeBridgeError(status_code, message).
The caller decides whether to retry or surface the error.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

INTAKE_URL = os.getenv("INTAKE_URL", "").rstrip("/")
BRIDGE_SECRET = os.getenv("BRIDGE_SECRET", "")

_DEFAULT_TIMEOUT = 10.0  # seconds


class IntakeBridgeError(Exception):
    """Raised when the intake bridge returns a non-2xx response or fails to reach the service."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[intake_bridge {status_code}] {message}")


def _headers() -> dict:
    if not BRIDGE_SECRET:
        # Surface a clear error rather than silently sending unauth requests.
        raise IntakeBridgeError(0, "BRIDGE_SECRET env var is not set")
    return {
        "X-Bridge-Secret": BRIDGE_SECRET,
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    if not INTAKE_URL:
        raise IntakeBridgeError(0, "INTAKE_URL env var is not set")
    return INTAKE_URL


async def _request(method: str, path: str, *, json_body: Optional[dict] = None,
                   params: Optional[dict] = None) -> httpx.Response:
    url = f"{_base_url()}{path}"
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, json=json_body, params=params)
    except httpx.TimeoutException as e:
        raise IntakeBridgeError(0, f"timeout contacting intake bridge: {e}") from e
    except httpx.HTTPError as e:
        raise IntakeBridgeError(0, f"network error contacting intake bridge: {e}") from e

    if resp.status_code < 200 or resp.status_code >= 300:
        # Include body for debuggability — bridge returns JSON errors typically.
        try:
            body = resp.text
        except Exception:
            body = "<unreadable>"
        raise IntakeBridgeError(resp.status_code, body)

    return resp


async def create_lead(payload: dict) -> dict:
    """POST /bridge/leads — create a new lead and trigger invite email.

    Required keys in `payload` (per bridge contract):
        first_name, last_name, email, phone, dob, appointment_at,
        created_by, creator_email, creator_id, intake_form_type
    Returns the full bridge response dict including patient_id, submission_id,
    link_token, intake_url, intake_form_type.
    """
    resp = await _request("POST", "/bridge/leads", json_body=payload)
    return resp.json()


async def list_leads(status: Optional[str] = None,
                     since: Optional[str] = None,
                     limit: int = 100) -> list[dict]:
    """GET /bridge/leads — list lead rows (rarely used; pills mostly cover this).

    Optional filters:
        status: pending | in_progress | submitted | purged
        since:  ISO-8601 timestamp lower bound
        limit:  max rows (default 100)
    """
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    if since:
        params["since"] = since
    resp = await _request("GET", "/bridge/leads", params=params)
    data = resp.json()
    # Bridge returns either a list or {leads: [...]} — accept both.
    if isinstance(data, dict) and "leads" in data:
        return data["leads"]
    if isinstance(data, list):
        return data
    return []


async def get_lead_detail(patient_id: str) -> dict:
    """GET /bridge/leads/{patient_id} — full detail incl. flags + summary_html."""
    if not patient_id:
        raise IntakeBridgeError(0, "patient_id is required")
    resp = await _request("GET", f"/bridge/leads/{patient_id}")
    return resp.json()


async def mark_converted(patient_id: str, firebase_uid: str) -> dict:
    """POST /bridge/leads/{patient_id}/mark_converted — link lead to Firebase UID and lock row."""
    if not patient_id:
        raise IntakeBridgeError(0, "patient_id is required")
    if not firebase_uid:
        raise IntakeBridgeError(0, "firebase_uid is required")
    resp = await _request(
        "POST",
        f"/bridge/leads/{patient_id}/mark_converted",
        json_body={"converted_to_uid": firebase_uid},
    )
    return resp.json()


async def delete_lead(patient_id: str) -> None:
    """DELETE /bridge/leads/{patient_id} — soft-delete."""
    if not patient_id:
        raise IntakeBridgeError(0, "patient_id is required")
    await _request("DELETE", f"/bridge/leads/{patient_id}")
