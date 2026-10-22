"""Server-only Telegram delivery. Never log request URLs: they contain the token."""
import os
import httpx


class TelegramNotifier:
    @property
    def enabled(self):
        return bool(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_CHAT_ID"))

    def send(self, application_id, application, salon):
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise RuntimeError("Telegram is not configured")
        labels = {
            "owner_name": "Владелец", "phone": "Телефон", "pet_name": "Питомец",
            "pet_breed": "Порода", "pet_age": "Возраст", "service": "Услуга",
            "preferred_date": "Желаемая дата", "preferred_time": "Желаемое время",
            "preferred_groomer": "Мастер", "comment": "Комментарий",
        }
        values = application.model_dump(mode="json")
        values["service"] = salon["services"][application.service]["name"]
        values["preferred_date"] = application.preferred_date.strftime("%d.%m.%Y")
        values["preferred_time"] = application.preferred_time.strftime("%H:%M")
        text = "🐾 Новая заявка — " + salon["name"] + "\n\n"
        text += "\n".join(f"{label}: {values[key]}" for key, label in labels.items())
        text += f"\n\nНомер: {application_id}\nЗаявка сохранена. Время необходимо согласовать с клиентом."
        # No parse_mode: customer text cannot inject HTML formatting.
        # Do not retry ambiguous timeouts: Telegram has no idempotency key.
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat_id, "text": text,
                    "link_preview_options": {"is_disabled": True},
                })
                response.raise_for_status()
                data = response.json()
                if not data.get("ok") or not isinstance(data.get("result", {}).get("message_id"), int):
                    raise ValueError("Delivery was not confirmed")
                return data["result"]["message_id"]
        except Exception:
            raise RuntimeError("Telegram delivery was not confirmed") from None
