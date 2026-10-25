"""Server-only Supabase REST access with retry-safe UUIDs."""
import os
from uuid import UUID
import httpx
from models import Application

class SupabaseStore:
    def save(self, application_id: str, application: Application, salon: dict) -> str:
        UUID(application_id)
        url = os.getenv("SUPABASE_URL", "").rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url.startswith("https://") or not key:
            raise RuntimeError("Supabase is not configured")
        row = application.model_dump(mode="json")
        row.update(id=application_id, status="new", service=salon["services"][application.service]["name"] if application.service else None)
        # Same ID is reused after a timeout: no duplicate even if the first insert committed.
        headers = {"apikey": key, "Authorization": f"Bearer {key}",
                   "Prefer": "resolution=ignore-duplicates,return=minimal"}
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.post(url + "/rest/v1/applications", headers=headers,
                                   params={"on_conflict": "id"}, json=row)
            response.raise_for_status()
            saved = client.get(url + "/rest/v1/applications", headers=headers,
                               params={"id": "eq." + application_id, "select": "id"})
            saved.raise_for_status()
            if saved.json() != [{"id": application_id}]:
                raise RuntimeError("Insert could not be verified")
        return application_id
