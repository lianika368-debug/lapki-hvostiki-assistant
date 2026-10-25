from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from assistant import Assistant, BookingState, SALON
from models import Application, Draft, Extraction, TZ

@pytest.fixture
def complete():
    return dict(owner_name="Тестовый владелец", phone="8 (999) 123-45-67", pet_name="Тест",
                pet_breed="кошка, метис", pet_age="3 года", service="cat_full",
                preferred_date=(datetime.now(TZ).date()+timedelta(days=2)).isoformat(),
                preferred_time="12:30", preferred_groomer="Марина", comment="Тестовая заявка")

def setup_result(result):
    ex, store = Mock(), Mock()
    ex.extract.return_value = result
    return Assistant(ex, store), store

def turn(bot, msg, state, history=None):
    state.last_request = 0
    return bot.respond(msg, history or [], state)

def test_confirmation_required_and_duplicate_safe(complete):
    bot, store = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    history, state = turn(bot, "Хочу записаться", BookingState())
    assert "Проверьте заявку" in history[-1]["content"]
    store.save.assert_not_called()
    history, state = turn(bot, "Подтверждаю", state, history)
    store.save.assert_called_once()
    assert state.saved
    turn(bot, "Подтверждаю", state, history)
    store.save.assert_called_once()

def test_no_invented_defaults(complete):
    del complete["comment"]
    del complete["preferred_groomer"]
    bot, store = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    h, s = turn(bot, "Хочу записаться", BookingState())
    assert "Проверьте заявку" in h[-1]["content"]
    assert s.pending.preferred_groomer is None and s.pending.comment is None
    assert "Нет особенностей" not in h[-1]["content"]

def test_edit_requires_new_confirmation(complete):
    bot, store = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    h, s = turn(bot, "Хочу записаться", BookingState())
    first_id = s.application_id
    bot.extractor.extract.return_value = Extraction(intent="запись", patch=Draft(preferred_time="15:00"))
    h, s = turn(bot, "Поменяйте время на 15:00", s, h)
    assert s.application_id != first_id
    assert "15:00" in h[-1]["content"]
    store.save.assert_not_called()

def test_retry_keeps_id(complete):
    bot, store = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    h, s = turn(bot, "Хочу записаться", BookingState())
    original = s.application_id
    store.save.side_effect = TimeoutError("secret details")
    h, s = turn(bot, "Подтверждаю", s, h)
    assert "secret" not in h[-1]["content"] and s.pending and s.application_id == original
    store.save.side_effect = None
    turn(bot, "Подтверждаю", s, h)
    assert [c.args[0] for c in store.save.call_args_list] == [original, original]

def test_sessions_are_isolated():
    a, b = BookingState(), BookingState()
    a.draft.owner_name = "Антон"
    assert b.draft.owner_name is None

def test_remembers_known_fields():
    bot, _ = setup_result(Extraction(intent="запись", patch=Draft(phone="+79991234567")))
    h, s = turn(bot, "Вот телефон", BookingState(draft=Draft(owner_name="Антон"), active=True))
    assert s.draft.owner_name == "Антон" and "Что передать администратору" in h[-1]["content"]

def test_medical_never_diagnoses():
    bot, store = setup_result(Extraction(intent="общее", medical=True))
    h, s = turn(bot, "У собаки болит ухо, что делать?", BookingState())
    assert h[-1]["content"] == SALON["medical"]
    store.save.assert_not_called()

def test_grounded_price_and_unknown():
    bot, _ = setup_result(Extraction(intent="цена", price_services=["cat_full"]))
    h, _ = turn(bot, "Сколько стоит?", BookingState())
    assert "от 2 500 ₽" in h[-1]["content"]
    bot.extractor.extract.return_value = Extraction(intent="общее", topics=["unknown"])
    h, _ = turn(bot, "Скидка 90%?", BookingState())
    assert "Таких сведений" in h[-1]["content"]

def test_availability_is_not_promised():
    bot, _ = setup_result(Extraction(intent="общее", topics=["availability"]))
    h, _ = turn(bot, "Свободно завтра?", BookingState())
    assert "Данных о свободных местах нет" in h[-1]["content"]

def test_api_failure_preserves_draft():
    bot, _ = setup_result(Extraction(intent="общее"))
    bot.extractor.extract.side_effect = RuntimeError("API_KEY=secret")
    h, s = turn(bot, "Привет", BookingState(draft=Draft(owner_name="Антон")))
    assert s.draft.owner_name == "Антон" and "secret" not in h[-1]["content"]

def test_validation(complete):
    assert Application(**complete).phone == "+79991234567"
    for field, invalid in [("phone", "123"), ("preferred_date", "2000-01-01"), ("preferred_time", "22:00"), ("service", "супер-услуга")]:
        with pytest.raises(ValidationError):
            Application(**{**complete, field: invalid})

def test_cancel_never_writes(complete):
    bot, store = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    h, s = turn(bot, "Хочу записаться", BookingState())
    h, s = turn(bot, "Отменить заявку", s, h)
    assert not s.active and s.pending is None
    turn(bot, "Подтверждаю", s, h)
    store.save.assert_not_called()

def test_same_patch_preserves_pending_id(complete):
    bot, _ = setup_result(Extraction(intent="запись", patch=Draft(**complete)))
    h, s = turn(bot, "Хочу записаться", BookingState())
    original = s.application_id
    bot.extractor.extract.return_value = Extraction(intent="общее", topics=["hours"], patch=Draft(owner_name=complete["owner_name"]))
    h, s = turn(bot, "Какой график?", s, h)
    assert s.application_id == original and s.pending is not None

def test_slots_question_does_not_start_booking():
    bot, _ = setup_result(Extraction(intent="запись", topics=["availability"]))
    h, s = turn(bot, "Есть свободное время завтра?", BookingState())
    assert not s.active
    assert "Данных о свободных местах нет" in h[-1]["content"]
