"""Full server path with simulated external HTTP: review -> DB -> Telegram."""
import json
from unittest.mock import Mock
import httpx
import pytest
from assistant import Assistant, BookingState
from models import Draft, Extraction
from storage import SupabaseStore
from notifications import TelegramNotifier


@pytest.mark.parametrize('telegram_status',[200,401])
def test_minimal_application_http_flow(monkeypatch,telegram_status,caplog):
    for key,value in {'SUPABASE_URL':'https://db.invalid','SUPABASE_SERVICE_ROLE_KEY':'fake-db-key',
                      'TELEGRAM_BOT_TOKEN':'fake:token','TELEGRAM_CHAT_ID':'123'}.items():
        monkeypatch.setenv(key,value)
    calls=[]; stored={}
    def handle(request):
        calls.append((request.url.host,request.method))
        if request.url.host=='db.invalid':
            if request.method=='POST':
                stored.update(json.loads(request.content))
                assert stored['owner_name'] is None and stored['preferred_time'] is None
                assert stored['service'] is None and stored['pet_weight']=='12 кг'
                assert 'Уточнить услугу' in stored['handoff']
                return httpx.Response(201)
            return httpx.Response(200,json=[{'id':stored['id']}])
        assert request.url.host=='api.telegram.org'
        payload=json.loads(request.content)
        assert '12 кг' in payload['text'] and 'в субботу после обеда' in payload['text']
        assert 'Уточнить услугу' in payload['text'] and 'а не подтверждённая запись' in payload['text']
        if telegram_status==401:return httpx.Response(401,json={'ok':False,'description':'Unauthorized'})
        return httpx.Response(200,json={'ok':True,'result':{'message_id':42}})
    client=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(handle),**kw))
    extractor=Mock()
    extractor.extract.return_value=Extraction(intent='запись',action='book',patch=Draft(
        phone='+79991234567',pet_breed='корги',pet_weight='12 кг',request_text='Комплекс',schedule_note='в субботу после обеда'))
    bot=Assistant(extractor,SupabaseStore(),notifier=TelegramNotifier())
    h,s=bot.respond('Хочу оставить заявку',[],BookingState())
    assert s.pending and not calls
    h,s=bot.respond('Подтверждаю',h,s,revision=s.revision)
    assert s.saved and 'Заявка сохранена' in h[-1]['content']
    assert calls==[('db.invalid','POST'),('db.invalid','GET'),('api.telegram.org','POST')]
    assert s.delivery==('confirmed' if telegram_status==200 else 'unconfirmed')
    if telegram_status==401:
        assert 'telegram_delivery_unconfirmed' in caplog.text and 'fake:token' not in caplog.text
    bot.respond('Подтверждаю',h,s,revision=s.revision)
    assert len(calls)==3
