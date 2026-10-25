"""Supabase failure must preserve the booking and never trigger Telegram."""
import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import httpx

from assistant import Assistant, BookingState, SALON, build_application
from models import Application, Draft, TZ
from storage import SupabaseStore


def test_supabase_failure_preserves_draft_without_success_or_telegram(monkeypatch):
    # A reserved .invalid address and MockTransport prevent real database access.
    monkeypatch.setenv("SUPABASE_URL", "https://supabase.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-only-key")
    requests = []

    def unavailable(request):
        assert request.url.host == "supabase.invalid"
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/applications"
        requests.append(json.loads(request.content))
        return httpx.Response(503, json={"message": "Simulated database outage"})

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(unavailable), **kwargs),
    )

    data = dict(
        owner_name="Тестовый владелец", phone="+79990000000", pet_name="Тест",
        pet_breed="кошка, метис", pet_age="3 года", service="cat_full",
        preferred_date=(datetime.now(TZ).date() + timedelta(days=2)).isoformat(),
        preferred_time="12:30", preferred_groomer="Марина", comment="Тестовая заявка",
    )
    application_id = "0c3275ab-b080-45aa-96e7-00d39bb24988"
    state = BookingState(
        draft=Draft(**data), active=True, pending=Application(**data),
        application_id=application_id,
    )
    state.pending = build_application(state, SALON)
    original_draft = state.draft.model_dump()
    original_pending = state.pending.model_dump()
    extractor, notifier = Mock(), Mock()
    notifier.enabled = True
    bot = Assistant(extractor, SupabaseStore(), notifier=notifier)
    history = []

    # Repeat confirmation to prove that the retained booking can be submitted again.
    for attempt in range(1, 3):
        history, state = bot.respond("Подтверждаю", history, state)
        reply = history[-1]["content"]
        assert "Не удалось подтвердить сохранение заявки" in reply
        assert "Повторите «Подтверждаю»" in reply
        assert "заявка сохранена" not in reply.casefold()
        assert state.draft.model_dump() == original_draft
        assert state.pending is not None
        assert state.pending.model_dump() == original_pending
        assert state.application_id == application_id
        assert state.active and not state.saved
        assert len(requests) == attempt
        notifier.send.assert_not_called()
        extractor.extract.assert_not_called()

    assert requests[0] == requests[1]
    assert requests[0]["id"] == application_id
    assert requests[0]["service"] == SALON["services"]["cat_full"]["name"]
