"""Call counts and session isolation are deterministic, unlike external API timings."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from assistant import Assistant, BookingState, SALON
from gemini_client import GeminiExtractor
from lapka import LapkaResponder
from models import Draft, Extraction
from ui_gate import run_session, _active_sessions


@pytest.mark.parametrize('message',['привет','Здравствуйте!','спасибо','ты так долго отвечаешь'])
def test_short_messages_use_generated_reply_and_preserve_draft(message):
    extractor, store, notifier = Mock(), Mock(), Mock()
    bot = Assistant(extractor, store, notifier=notifier, responder=LapkaResponder())
    extractor.extract.return_value = Extraction(intent='общее', reply='Помогу с вопросами об уходе — что хотите уточнить?')
    state = BookingState(active=True,draft=Draft(service='nails'))
    bot._prepare(state)
    original = (state.draft.model_dump(),state.application_id,state.revision,state.pending)
    h, state = bot.respond(message, [], state)
    extractor.extract.assert_called_once()
    store.save.assert_not_called();notifier.send.assert_not_called()
    assert h[-1]['content'] == extractor.extract.return_value.reply
    assert (state.draft.model_dump(),state.application_id,state.revision,state.pending)==original


def test_greeting_with_booking_details_still_goes_through_extraction():
    extractor, store = Mock(), Mock()
    extractor.extract.return_value=Extraction(intent='запись',action='book',patch=Draft(service='nails'))
    bot=Assistant(extractor,store,responder=LapkaResponder())
    h,state=bot.respond('Привет, хочу записаться на стрижку когтей',[],BookingState())
    extractor.extract.assert_called_once()
    assert state.active and state.awaiting_field=='phone'


def test_one_real_sdk_call_contains_extraction_and_reply(monkeypatch):
    import gemini_client
    monkeypatch.setenv('GEMINI_API_KEY','unit-test-key')
    client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
    client.models.generate_content.return_value.text=json.dumps({
        'intent':'цена','price_services':['cat_full'],
        'reply':'Комплексный груминг для кошек: от 2 500 ₽. Стоимость ориентировочная.'})
    constructor=Mock(return_value=client)
    monkeypatch.setattr(gemini_client.genai,'Client',constructor)
    store,notifier=Mock(),Mock()
    bot=Assistant(GeminiExtractor(),store,notifier=notifier,responder=LapkaResponder())
    h,state=bot.respond('Сколько стоит комплекс для кошки?',[],BookingState())
    constructor.assert_called_once();client.models.generate_content.assert_called_once()
    assert h[-1]['content']=='Комплексный груминг для кошек: от 2 500 ₽. Стоимость ориентировочная.'
    assert constructor.call_args.kwargs['http_options'].retry_options.attempts==1
    assert constructor.call_args.kwargs['http_options'].timeout==20000
    assert str(client.models.generate_content.call_args.kwargs['config'].thinking_config.thinking_level).endswith('MINIMAL')
    store.save.assert_not_called();notifier.send.assert_not_called()


def retry_client(monkeypatch, failures, spent=3):
    import gemini_client
    monkeypatch.setenv('GEMINI_API_KEY','unit-test-key')
    clock=[0.0]
    monkeypatch.setattr(gemini_client,'time',SimpleNamespace(
        monotonic=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
    client=Mock();client.__enter__=Mock(return_value=client);client.__exit__=Mock(return_value=False)
    def response(**kwargs):
        clock[0]+=spent
        if failures:
            raise failures.pop(0)
        return SimpleNamespace(text='{"intent":"общее"}')
    client.models.generate_content.side_effect=response
    monkeypatch.setattr(gemini_client.genai,'Client',Mock(return_value=client))
    return client


def test_one_transient_retry_uses_remaining_time_not_a_new_budget(monkeypatch):
    temporary=type('TemporaryAPIError',(Exception,),{'code':503})
    client=retry_client(monkeypatch,[temporary()])
    extracted=GeminiExtractor().extract('Вопрос',[],Draft(),SALON)
    assert extracted._api_attempts==2
    calls=client.models.generate_content.call_args_list
    assert len(calls)==2
    assert calls[0].kwargs['config'].http_options.timeout==20000
    assert 16800<=calls[1].kwargs['config'].http_options.timeout<=16900
    assert '_api_attempts' not in extracted.model_dump()


def test_transient_error_does_not_loop_beyond_one_retry(monkeypatch):
    temporary=type('TemporaryAPIError',(Exception,),{'code':503})
    client=retry_client(monkeypatch,[temporary(),temporary(),temporary()])
    with pytest.raises(temporary):GeminiExtractor().extract('Вопрос',[],Draft(),SALON)
    assert client.models.generate_content.call_count==2


def test_insufficient_budget_or_timeout_does_not_retry(monkeypatch):
    temporary=type('TemporaryAPIError',(Exception,),{'code':503})
    client=retry_client(monkeypatch,[temporary()],spent=18.8)
    with pytest.raises(temporary):GeminiExtractor().extract('Вопрос',[],Draft(),SALON)
    client.models.generate_content.assert_called_once()
    client=retry_client(monkeypatch,[TimeoutError()],spent=3)
    with pytest.raises(TimeoutError):GeminiExtractor().extract('Вопрос',[],Draft(),SALON)
    client.models.generate_content.assert_called_once()


def test_same_session_cannot_race_but_another_session_is_not_blocked():
    entered,release=Event(),Event()
    first=SimpleNamespace(session_hash='session-one')
    other=SimpleNamespace(session_hash='session-two')
    def blocked():
        entered.set()
        assert release.wait(3)
        return 'completed'
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(run_session,first,blocked,lambda:'busy')
        try:
            assert entered.wait(2)
            assert run_session(first,lambda:pytest.fail('same-session action ran'),lambda:'busy')=='busy'
            assert run_session(other,lambda:'other completed',lambda:'busy')=='other completed'
        finally:
            release.set()
        assert pending.result(timeout=2)=='completed'
    assert not _active_sessions


def test_session_gate_is_released_after_error():
    request=SimpleNamespace(session_hash='error-session')
    def fail():
        raise RuntimeError('fake')
    with pytest.raises(RuntimeError):run_session(request,fail,lambda:'busy')
    assert run_session(request,lambda:'retry',lambda:'busy')=='retry'
    assert not _active_sessions


def test_busy_ui_response_preserves_input_history_draft_and_revision():
    from app import _busy_result,outputs,demo
    result=_busy_result()
    assert len(result)==len(outputs)==19
    assert 'обрабатывается' in result[9]
    assert all(value=={'__type__':'update'} for index,value in enumerate(result) if index!=9)
    chat_events=[fn for fn in demo.fns.values() if fn.concurrency_id=='chat']
    assert chat_events and all(fn.concurrency_limit==4 for fn in chat_events)
