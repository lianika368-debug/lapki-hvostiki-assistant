"""Booking is one local form, with no AI call or persistence before confirmation."""
from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest

import app
from assistant import Assistant, BookingState
from models import Draft, Extraction, TZ
from quick_booking import is_booking_start


@pytest.fixture
def offline(monkeypatch):
    extractor, store, notifier = Mock(), Mock(), Mock()
    notifier.enabled = True
    bot = Assistant(extractor, store, notifier=notifier)
    monkeypatch.setattr(app, 'assistant', bot)
    return bot


@pytest.mark.parametrize('message', ['Хочу записаться', 'Запишите меня!', 'Можно записаться?',
                                      'Здравствуйте, хочу записаться', 'Хочу оставить заявку'])
def test_booking_request_immediately_shows_both_fields_without_api(offline, message):
    frames = list(app.send_message(message, [], BookingState()))
    assert len(frames) == 1
    result = frames[0]
    assert result[13]['visible'] and not result[12]['visible']
    assert 'телефона' in result[1][-1]['content'] and 'услуги' in result[1][-1]['content']
    assert result[0]['value'] == ''
    offline.extractor.extract.assert_not_called()
    offline.store.save.assert_not_called(); offline.notifier.send.assert_not_called()


@pytest.mark.parametrize('message', ['Не хочу записываться', 'Сколько стоит стрижка?',
                                      'Можно записаться, если у кота зуд?'])
def test_start_shortcut_never_swallows_details_or_medical_questions(message):
    assert not is_booking_start(message)


def test_recognized_detailed_booking_also_opens_prefilled_form(offline):
    offline.extractor.extract.return_value = Extraction(intent='запись', action='book',
                                                         patch=Draft(service='nails', pet_name='Тест'))
    result = app._send('Поможете подобрать и оформить услугу для кота Тест?', [], BookingState())
    assert result[13]['visible'] and result[15]['value'] == 'Стрижка когтей'
    assert result[14]['value'] == ''
    offline.extractor.extract.assert_called_once()


def test_form_review_and_confirmation_work_without_any_gemini_call(offline):
    state = BookingState(draft=Draft(pet_name='Плюш', pet_type='кошка', owner_name='Тест'))
    opened = app.start_application([], state)
    ready = app.prepare_form('+7 (999) 000-00-00', 'Стрижка когтей', '', '', opened[2], state)
    assert state.pending and not state.saved
    assert state.pending.phone == '+79990000000' and state.pending.service == 'nails'
    assert state.pending.pet_name == 'Плюш' and state.pending.owner_name == 'Тест'
    assert ready[4]['visible'] and not ready[13]['visible'] and not ready[12]['visible']
    offline.store.save.assert_not_called(); offline.notifier.send.assert_not_called()
    confirmed = list(app.preset('Подтверждаю', ready[2], state, ready[8]))[-1]
    assert confirmed[3].saved
    offline.extractor.extract.assert_not_called()
    offline.store.save.assert_called_once(); offline.notifier.send.assert_called_once()
    list(app.preset('Подтверждаю', confirmed[2], state, confirmed[8]))
    offline.store.save.assert_called_once(); offline.notifier.send.assert_called_once()


def test_bad_form_keeps_raw_values_and_cannot_confirm_old_review(offline):
    state = BookingState(active=True, draft=Draft(phone='+79991234567', service='nails'))
    offline._prepare(state)
    original = state.draft.model_dump()
    response = app.prepare_form('123', 'Нужен уход', 'На выходных', '<b>Пугливый</b>', [], state)
    assert not state.pending and not state.revision
    assert state.draft.model_dump() == original
    assert response[13]['visible'] and 'Проверьте телефон' in response[18]
    assert [value['value'] for value in response[14:18]] == ['123', 'Нужен уход', 'На выходных', '<b>Пугливый</b>']
    list(app.preset('Подтверждаю', response[2], state, response[8]))
    offline.store.save.assert_not_called(); offline.notifier.send.assert_not_called()
    offline.extractor.extract.assert_not_called()


def test_form_reports_missing_fields_together_and_preserves_unsent_chat(offline):
    response = app.prepare_form('', '', '', '', [], BookingState())
    assert 'телефон' in response[18] and 'услугу' in response[18]
    assert response[0] == {'__type__':'update'}
    assert response[13]['visible']
    offline.extractor.extract.assert_not_called(); offline.store.save.assert_not_called()


def test_existing_facts_are_prefilled_and_chat_does_not_reset_unsubmitted_form(offline):
    state = BookingState(draft=Draft(phone='+79991234567', service='cat_full', comment='Боится фена'))
    state.editing = True
    opened = app.start_application([], state)
    assert opened[14]['value'] == '+79991234567'
    assert opened[15]['value'] == 'Комплексный груминг для кошек'
    assert opened[17]['value'] == 'Боится фена'
    refreshed = app.quick_answer('included', opened[2], state)
    assert all(value == {'__type__':'update'} for value in refreshed[14:18])
    other = app.start_application([], BookingState())
    assert other[14]['value'] == '' and other[17]['value'] == ''


@pytest.mark.parametrize('when', ['25:99', '09:00', '01.01.2020'])
def test_invalid_optional_time_is_rejected_without_api(offline, when):
    state = BookingState()
    result = app.prepare_form('+79990000000', 'Стрижка когтей', when, '', [], state)
    assert result[13]['visible'] and not state.pending and result[18] != '<p role="alert"></p>'
    offline.extractor.extract.assert_not_called(); offline.store.save.assert_not_called()


def test_optional_time_and_features_are_retained_without_separate_questions(offline):
    day = (datetime.now(TZ).date() + timedelta(days=2)).strftime('%d.%m.%Y')
    state = BookingState()
    result = app.prepare_form('+79990000000', 'Помочь выбрать уход', day+' 12:30', 'Кот Плюш, боится фена', [], state)
    assert result[4]['visible'] and state.pending.preferred_time.strftime('%H:%M') == '12:30'
    assert state.pending.comment == 'Кот Плюш, боится фена'
    assert not state.pending.service and state.pending.request_text == 'Помочь выбрать уход'
    offline.extractor.extract.assert_not_called(); offline.store.save.assert_not_called()


def test_storage_failure_keeps_ready_form_data_and_does_not_notify(offline):
    state = BookingState()
    ready = app.prepare_form('+79990000000', 'Стрижка когтей', '', 'Для мастера', [], state)
    original = state.draft.model_dump()
    offline.store.save.side_effect = RuntimeError('fake-db')
    failed = list(app.preset('Подтверждаю', ready[2], state, ready[8]))[-1]
    assert not state.saved and state.pending and state.draft.model_dump() == original
    assert failed[4]['visible'] and not failed[13]['visible']
    offline.notifier.send.assert_not_called(); offline.extractor.extract.assert_not_called()



def test_edit_opens_prefilled_form_without_another_conversation(offline):
    state = BookingState()
    ready = app.prepare_form('+79990000000', 'Стрижка когтей', '', '', [], state)
    edited = list(app.preset('Изменить заявку', ready[2], state, ready[8]))[-1]
    assert edited[13]['visible'] and not edited[4]['visible']
    assert edited[14]['value'] == '+79990000000' and edited[15]['value'] == 'Стрижка когтей'
    assert 'поля в форме' in edited[1][-1]['content']
    offline.extractor.extract.assert_not_called(); offline.store.save.assert_not_called()



@pytest.mark.parametrize('message', [
    'Хочу записаться на стрижку когтей, телефон +7 999 000 00 00',
    'Хочу записать кошку, телефон +7 999 000 00 00',
    'Запишите меня на завтра в 15:00, телефон +7 999 000 00 00',
])
def test_detailed_explicit_booking_opens_form_and_keeps_all_facts(offline, message):
    state = BookingState()
    response = app._send(message, [], state)
    assert response[13]['visible'] and response[14]['value'] == '+7 999 000 00 00'
    assert message in (response[15]['value'] + response[17]['value'])
    assert not state.pending
    if 'стрижку когтей' in message:
        assert response[15]['value'] == 'Стрижка когтей'
    ready = app.prepare_form(*(value['value'] for value in response[14:18]), response[2], state)
    assert ready[4]['visible'] and state.pending
    assert message in ((state.pending.request_text or '') + (state.pending.comment or ''))
    offline.extractor.extract.assert_not_called(); offline.store.save.assert_not_called()


def test_booking_does_not_choose_a_dog_size_from_breed(offline):
    response = app._send('Хочу записать корги на комплекс', [], BookingState())
    state = response[3]
    ready = app.prepare_form('+79990000000', response[15]['value'], '', response[17]['value'], response[2], state)
    assert ready[4]['visible'] and not state.draft.pet_size and not state.draft.service
    offline.extractor.extract.assert_not_called()
