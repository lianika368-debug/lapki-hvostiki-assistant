import json
from datetime import datetime, timedelta
from unittest.mock import Mock
import httpx
import pytest
from assistant import Assistant, BookingState, SALON
from models import Application, Draft, TZ
from notifications import TelegramNotifier


def application():
    return Application(owner_name="ТЕСТ", phone="+79990000000", pet_name="Тест",
                       pet_breed="Кошка", pet_age="3 года", service="cat_full",
                       preferred_date=datetime.now(TZ).date()+timedelta(days=3),
                       preferred_time="12:00", preferred_groomer="Марина", comment="<b>ТЕСТ</b>")


def test_delivery_only_after_save_and_confirmation():
    store, notifier = Mock(), Mock()
    bot = Assistant(Mock(), store, notifier=notifier)
    pending = application()
    state = BookingState(draft=Draft(**pending.model_dump(mode='json',exclude={'handoff'})), active=True)
    bot._prepare(state)
    state.application_id = 'test-id'
    pending = state.pending
    store.save.side_effect = RuntimeError("database unavailable")
    bot.respond("Подтверждаю", [], state)
    notifier.send.assert_not_called()
    store.save.side_effect = None
    notifier.send.side_effect = lambda *_: store.save.assert_called()
    bot.respond("Подтверждаю", [], state)
    notifier.send.assert_called_once_with("test-id", pending, SALON)
    bot.respond("Подтверждаю", [], state)
    notifier.send.assert_called_once()


def test_telegram_failure_does_not_lose_saved_application(caplog):
    notifier = Mock()
    notifier.send.side_effect = RuntimeError("SECRET_TOKEN")
    bot = Assistant(Mock(), Mock(), notifier=notifier)
    state = BookingState(draft=Draft(**application().model_dump(mode='json',exclude={'handoff'})), active=True)
    bot._prepare(state)
    state.application_id = 'test-id'
    history, state = bot.respond("Подтверждаю", [], state)
    assert state.saved and "Заявка сохранена" in history[-1]["content"]
    assert "SECRET_TOKEN" not in caplog.text
    assert "telegram_delivery_unconfirmed application_id=test-id" in caplog.text


def test_telegram_request_plain_text_and_safe_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    real_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert TelegramNotifier().send("test-id", application(), SALON) == 42
    payload = requests[0]
    assert payload["chat_id"] == "123" and "parse_mode" not in payload
    assert "<b>ТЕСТ</b>" in payload["text"]
    assert "Комплексный груминг для кошек" in payload["text"] and len(payload["text"]) < 4096
    assert "12:00" in payload["text"]
    monkeypatch.setattr(httpx, "Client", Mock(side_effect=RuntimeError("secret url")))
    with pytest.raises(RuntimeError, match="^Telegram delivery was not confirmed$"):
        TelegramNotifier().send("test-id", application(), SALON)
