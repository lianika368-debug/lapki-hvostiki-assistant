"""Quick actions must preserve booking state and never bypass confirmation."""
from datetime import datetime, timedelta
from assistant import BookingState, SALON
from models import Draft, Application, TZ
from app import quick_answer, controls, clear


def test_faq_preserves_draft():
    state = BookingState(active=True, draft=Draft(owner_name="Тест"))
    response = quick_answer("included", [], state)
    assert response[3] is state
    assert response[3].draft.owner_name == "Тест"
    assert response[1][-1]["content"] == SALON["included"]
    assert response[4]["visible"] is False


def test_suggestions_only_for_current_field():
    values = dict(owner_name="Тест", phone="+79990000000", pet_name="Кот", pet_breed="Метис", pet_age="3 года", service="cat_full", preferred_date=(datetime.now(TZ).date()+timedelta(days=2)).isoformat(), preferred_time="12:00")
    state = BookingState(active=True, draft=Draft(**values))
    confirm, groomer, comment = controls(state)
    assert not confirm["visible"] and groomer["visible"] and not comment["visible"]
    state.draft.preferred_groomer = "Без предпочтений"
    assert controls(state)[2]["visible"]
    state.draft.comment = "Нет особенностей"
    state.pending = Application.model_validate(state.draft.model_dump())
    assert controls(state)[0]["visible"]
    state.saved = True
    assert not controls(state)[0]["visible"]


def test_reset_hides_actions_and_forgets_draft():
    response = clear()
    assert response[3].draft == Draft()
    assert all(not update["visible"] for update in response[4:])
