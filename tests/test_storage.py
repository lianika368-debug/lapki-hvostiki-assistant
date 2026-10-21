from datetime import datetime, timedelta
from uuid import uuid4
import httpx
from models import Application, TZ
from assistant import SALON
from storage import SupabaseStore

def test_server_insert_is_idempotent(monkeypatch):
    calls = []
    def handle(request):
        calls.append(request)
        if request.method == "POST":
            assert request.headers["authorization"] == "Bearer server-secret"
            assert "ignore-duplicates" in request.headers["prefer"]
            return httpx.Response(201)
        return httpx.Response(200, json=[{"id": app_id}])
    real_client = httpx.Client
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "server-secret")
    monkeypatch.setattr("storage.httpx.Client", lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    app_id = str(uuid4())
    app = Application(owner_name="Тест", phone="+79991234567", pet_name="Тест", pet_breed="Кошка", pet_age="2 года", service="cat_full", preferred_date=datetime.now(TZ).date()+timedelta(days=2), preferred_time="12:00", preferred_groomer="Марина", comment="Нет особенностей")
    assert SupabaseStore().save(app_id, app, SALON) == app_id
    assert len(calls) == 2
