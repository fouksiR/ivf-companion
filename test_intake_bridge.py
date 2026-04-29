"""Unit tests for intake_bridge — HTTP layer mocked, no real network calls."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Set env before import so module-level reads pick them up.
os.environ.setdefault("INTAKE_URL", "https://intake.test.example/")
os.environ.setdefault("BRIDGE_SECRET", "test-secret-123")

import intake_bridge  # noqa: E402


def _make_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data if json_data is not None else {})
    resp.text = text or (str(json_data) if json_data is not None else "")
    return resp


class _AsyncClientCtx:
    """Async context manager that returns a mock with .request() = AsyncMock."""

    def __init__(self, response):
        self.response = response
        self.request = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_client(response):
    """Return a patcher for httpx.AsyncClient that yields our fake context."""
    ctx = _AsyncClientCtx(response)
    return patch.object(intake_bridge.httpx, "AsyncClient", MagicMock(return_value=ctx)), ctx


class IntakeBridgeTests(unittest.TestCase):
    def test_create_lead_success(self):
        payload = {
            "first_name": "Test",
            "last_name": "Patient",
            "email": "fouksi@gmail.com",
            "phone": "+61400000000",
            "dob": "1990-01-01",
            "appointment_at": "2026-05-15T09:00:00+10:00",
            "created_by": "secretary@example.com",
            "creator_email": "secretary@example.com",
            "creator_id": "fb-uid-abc123",
            "intake_form_type": "first_consult",
        }
        bridge_response = {
            "patient_id": "lead-uuid-1",
            "submission_id": "sub-uuid-1",
            "link_token": "tok-xyz",
            "intake_url": "https://intake.test.example/form/tok-xyz",
            "intake_form_type": "first_consult",
        }
        resp = _make_response(200, bridge_response)
        patcher, ctx = _patch_client(resp)
        with patcher:
            result = asyncio.run(intake_bridge.create_lead(payload))

        self.assertEqual(result, bridge_response)
        ctx.request.assert_awaited_once()
        args, kwargs = ctx.request.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://intake.test.example/bridge/leads")
        self.assertEqual(kwargs["json"], payload)
        self.assertEqual(kwargs["headers"]["X-Bridge-Secret"], "test-secret-123")

    def test_list_leads_with_status(self):
        rows = [{"patient_id": "p1"}, {"patient_id": "p2"}]
        resp = _make_response(200, rows)
        patcher, ctx = _patch_client(resp)
        with patcher:
            result = asyncio.run(intake_bridge.list_leads(status="pending", limit=50))
        self.assertEqual(result, rows)
        _, kwargs = ctx.request.call_args
        self.assertEqual(kwargs["params"], {"limit": 50, "status": "pending"})

    def test_list_leads_accepts_dict_envelope(self):
        envelope = {"leads": [{"patient_id": "p1"}]}
        resp = _make_response(200, envelope)
        patcher, _ = _patch_client(resp)
        with patcher:
            result = asyncio.run(intake_bridge.list_leads())
        self.assertEqual(result, envelope["leads"])

    def test_get_lead_detail(self):
        detail = {
            "patient": {"id": "p1"},
            "submission": {"status": "submitted"},
            "flags": {"priority": [], "address": [], "note": []},
            "summary_html": "<p>summary</p>",
        }
        resp = _make_response(200, detail)
        patcher, ctx = _patch_client(resp)
        with patcher:
            result = asyncio.run(intake_bridge.get_lead_detail("p1"))
        self.assertEqual(result, detail)
        args, _ = ctx.request.call_args
        self.assertEqual(args[1], "https://intake.test.example/bridge/leads/p1")

    def test_mark_converted(self):
        resp = _make_response(200, {"ok": True})
        patcher, ctx = _patch_client(resp)
        with patcher:
            result = asyncio.run(intake_bridge.mark_converted("p1", "fb-uid"))
        self.assertEqual(result, {"ok": True})
        _, kwargs = ctx.request.call_args
        self.assertEqual(kwargs["json"], {"converted_to_uid": "fb-uid"})

    def test_delete_lead(self):
        resp = _make_response(204, None)
        patcher, ctx = _patch_client(resp)
        with patcher:
            asyncio.run(intake_bridge.delete_lead("p1"))
        args, _ = ctx.request.call_args
        self.assertEqual(args[0], "DELETE")
        self.assertEqual(args[1], "https://intake.test.example/bridge/leads/p1")

    def test_non_2xx_raises(self):
        resp = _make_response(403, {"detail": "forbidden"}, text='{"detail":"forbidden"}')
        patcher, _ = _patch_client(resp)
        with patcher:
            with self.assertRaises(intake_bridge.IntakeBridgeError) as cm:
                asyncio.run(intake_bridge.get_lead_detail("p1"))
        self.assertEqual(cm.exception.status_code, 403)

    def test_missing_secret_raises(self):
        with patch.object(intake_bridge, "BRIDGE_SECRET", ""):
            with self.assertRaises(intake_bridge.IntakeBridgeError):
                asyncio.run(intake_bridge.get_lead_detail("p1"))

    def test_missing_url_raises(self):
        with patch.object(intake_bridge, "INTAKE_URL", ""):
            with self.assertRaises(intake_bridge.IntakeBridgeError):
                asyncio.run(intake_bridge.get_lead_detail("p1"))


if __name__ == "__main__":
    unittest.main()
