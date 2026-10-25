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
    state = BookingState(active=True,draft=Draft(phone='+79991234567',service='nails',comment='<script>alert(1)</script>'))
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
