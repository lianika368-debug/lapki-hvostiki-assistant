"""UI actions preserve context and use only the current server-side review."""
from assistant import Assistant, BookingState, SALON
from models import Draft
from unittest.mock import Mock
from app import quick_answer, controls, clear, request_reset, cancel_reset, result


def test_faq_preserves_draft():
    state = BookingState(active=True, draft=Draft(owner_name='Тест'))
    response = quick_answer('included', [], state)
    assert response[3] is state and state.draft.owner_name == 'Тест'
    assert response[1][-1]['content'] == SALON['included']
    assert not response[4]['visible']
    assert 'included' in state.explained


def test_review_and_context_buttons_follow_state():
    state = BookingState(active=True, draft=Draft(phone='+79991234567',service='cat_full'))
    Assistant(Mock(),Mock())._prepare(state)
    assert controls(state)[0]['visible']
    state.issues['preferred_time'] = 'Неверное время'
    assert not controls(state)[0]['visible'] and controls(state)[1]['visible']
    state.issues.clear()
    state.saved = True
    assert not controls(state)[0]['visible']


def test_reset_requires_confirmation_and_clears_everything():
    state = BookingState(active=True, draft=Draft(owner_name='Тест'), issues={'phone':'проверить'})
    history = [{'role':'user','content':'Тест'}]
    pending = request_reset(history,state)
    assert pending[11]['visible'] and state.draft.owner_name == 'Тест'
    assert not cancel_reset(history,state)[11]['visible']
    response = clear()
    assert response[3].draft == Draft() and not response[3].issues
    assert not any(update['visible'] for update in response[4:7])
    assert response[8] == '' and not response[11]['visible']


def test_error_preserves_input_and_summary_escapes_html():
    state = BookingState(last_error=True)
    assert result([],state,'непотерянный текст')[0] == 'непотерянный текст'
    state = BookingState(active=True,draft=Draft(phone='+79991234567',service='nails',pet_name='<script>alert(1)</script>'))
    Assistant(Mock(),Mock())._prepare(state)
    response = result([],state)
    assert '<script>' not in response[7] and '&lt;script&gt;' in response[7]


def test_direct_gradio_call_cannot_bypass_queue_or_supply_fake_state():
    from fastapi.testclient import TestClient
    from app import server, demo
    client=TestClient(server)
    fn_index=next(iter(demo.fns))
    response=client.post('/gradio_api/run/predict',json={
        'fn_index':fn_index, 'session_hash':'security-test-session',
        'data':['Подтверждаю',[],{'pending':{'phone':'+79991234567'},'saved':False},'forged-version']})
    assert response.status_code==404
    assert demo.api_open is False


def test_summary_is_shown_once_with_full_server_history_retained():
    from assistant import summary
    s=BookingState(active=True,draft=Draft(phone='+79991234567',service='cat_full'))
    Assistant(Mock(),Mock())._prepare(s)
    full=summary(s.pending,SALON)
    response=result([{'role':'assistant','content':full}],s)
    assert len(response[1][0]['content'])<len(full)
    assert response[2][0]['content']==full
    assert response[7].count('2 500')==1



def test_message_input_clears_before_work_and_status_distinguishes_draft(monkeypatch):
    import app
    from app import send_message
    from models import Extraction
    extractor, store, notifier = Mock(), Mock(), Mock()
    extractor.extract.return_value = Extraction(intent='запись', action='book', patch=Draft(service='nails'))
    monkeypatch.setattr(app, 'assistant', Assistant(extractor, store, notifier=notifier))
    stream = send_message('Поможете оформить уход для кота?', [], BookingState())
    pending = next(stream)
    assert pending[0]['value'] == '' and pending[0]['interactive'] is False
    assert 'готовит ответ' in pending[9]
    assert pending[1][-2] == {'role':'user', 'content':'Поможете оформить уход для кота?'}
    assert pending[1][-1] == {'role':'assistant', 'content':app.TYPING_MESSAGE}
    assert 'data-thinking="true"' in pending[9]
    extractor.extract.assert_not_called()
    completed = next(stream)
    assert completed[0]['value'] == '' and completed[0]['interactive'] is True
    assert 'ещё не отправлен' in completed[9]
    assert not any(item['content'] == app.TYPING_MESSAGE for item in completed[1] + completed[2])
    assert 'data-thinking' not in completed[9]
    store.save.assert_not_called(); notifier.send.assert_not_called()


def test_message_input_is_restored_on_api_failure(monkeypatch):
    import app
    from app import send_message
    extractor, store, notifier = Mock(), Mock(), Mock()
    extractor.extract.side_effect = TimeoutError('fake')
    monkeypatch.setattr(app, 'assistant', Assistant(extractor, store, notifier=notifier))
    state = BookingState(draft=Draft(pet_name='Плюш'))
    frames = list(send_message('Что входит в комплекс?', [], state))
    assert frames[0][0]['value'] == ''
    assert frames[-1][0]['value'] == 'Что входит в комплекс?'
    assert frames[-1][0]['interactive'] is True and state.draft.pet_name == 'Плюш'
    assert 'не отправлена' in frames[-1][9]
    assert not any(item['content'] == app.TYPING_MESSAGE for item in frames[-1][1] + frames[-1][2])
    assert 'data-thinking' not in frames[-1][9]
    store.save.assert_not_called(); notifier.send.assert_not_called()


def test_stream_holds_session_lock_until_result_and_releases_on_close(monkeypatch):
    from types import SimpleNamespace
    from app import send_message, clear
    from ui_gate import _active_sessions
    request = SimpleNamespace(session_hash='stream-test')
    stream = send_message('Что ты умеешь?', [], BookingState(), request=request)
    next(stream)
    assert 'stream-test' in _active_sessions
    assert 'обрабатывается' in clear(request=request)[9]
    stream.close()
    assert 'stream-test' not in _active_sessions


def test_ready_and_saved_status_cannot_be_confused_with_sent_message():
    state = BookingState(active=True, draft=Draft(phone='+79991234567', service='nails'))
    Assistant(Mock(), Mock())._prepare(state)
    assert 'ещё не отправлена' in result([], state)[9]
    state.saved = True
    assert 'сохранена' in result([], state)[9]
    assert 'не подтверждённая запись' in result([], state)[9]



def test_typing_indicator_also_disappears_on_unexpected_callback_error(monkeypatch):
    import app
    monkeypatch.setattr(app, '_send', Mock(side_effect=RuntimeError('fake')))
    frames = list(app.send_message('Вопрос', [], BookingState()))
    assert frames[0][1][-1]['content'] == app.TYPING_MESSAGE
    assert frames[-1][0]['value'] == 'Вопрос'
    assert frames[-1][0]['interactive'] is True
    assert not any(item['content'] == app.TYPING_MESSAGE for item in frames[-1][1] + frames[-1][2])
    assert 'data-thinking' not in frames[-1][9]



def test_prepare_button_disappears_when_review_is_ready_and_returns_on_edit():
    state = BookingState(active=True, draft=Draft(phone='+79991234567', service='nails'))
    Assistant(Mock(), Mock())._prepare(state)
    assert result([], state)[4]['visible']
    assert not result([], state)[12]['visible']
    state.pending = None
    state.editing = True
    editing = result([], state)
    assert not editing[12]['visible'] and editing[13]['visible']
    state.saved = True
    assert result([], state)[12]['value'] == 'Новая заявка'


def test_prepare_button_uses_known_details_without_api_and_requires_confirmation(monkeypatch):
    import app
    extractor, store, notifier = Mock(), Mock(), Mock()
    monkeypatch.setattr(app, 'assistant', Assistant(extractor, store, notifier=notifier))
    state = BookingState(draft=Draft(phone='+79991234567', service='nails', comment='Для мастера'), explained=['preparation'])
    response = app.start_application([], state)
    assert response[4]['visible'] and not response[12]['visible']
    assert response[0] == {'__type__':'update'}
    assert state.pending and not state.saved
    assert 'Обсуждалось' in state.pending.handoff
    assert 'Обсудили' not in response[7] and 'Для мастера' not in response[7]
    assert 'Обсуждалось' not in response[1][-1]['content']
    extractor.extract.assert_not_called(); store.save.assert_not_called(); notifier.send.assert_not_called()
    app.assistant.notifier.enabled = False
    completed = list(app.preset('Подтверждаю', response[2], state, response[8]))[-1]
    assert completed[3].saved
    store.save.assert_called_once()
    assert store.save.call_args.args[1].comment == 'Для мастера'
    assert 'объяснена подготовка' in store.save.call_args.args[1].handoff


def test_prepare_button_asks_only_for_missing_required_field(monkeypatch):
    import app
    extractor, store = Mock(), Mock()
    monkeypatch.setattr(app, 'assistant', Assistant(extractor, store))
    state = BookingState(draft=Draft(service='nails'))
    response = app.start_application([], state)
    assert state.awaiting_field == 'phone' and not state.pending
    assert not response[12]['visible'] and response[13]['visible']
    assert response[15]['value'] == 'Стрижка когтей'
    extractor.extract.assert_not_called(); store.save.assert_not_called()
