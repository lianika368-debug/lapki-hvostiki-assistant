"""Explicit routing, isolated draft, confirmation state machine and grounded replies."""
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
import html
import json
import logging
import re
import time
from pydantic import ValidationError
from models import Application, Draft, Extraction

SALON = json.loads(Path(__file__).with_name("salon.json").read_text())
QUESTIONS = {
    "owner_name": "Как зовут владельца?",
    "phone": "Укажите телефон владельца с кодом страны, например +7 (999) 123-45-67.",
    "pet_name": "Как зовут питомца?",
    "pet_breed": "Какая порода питомца? Для метиса так и укажите, а также кошка это или собака.",
    "pet_age": "Сколько питомцу лет или месяцев?",
    "service": "Какую услугу хотите выбрать? Для комплексного груминга собаки уточните размер: мелкий, средний или крупный.",
    "preferred_date": "На какую дату хотите оставить заявку? Укажите день, месяц и год.",
    "preferred_time": "Какое время предпочитаете? Салон работает с 10:00 до 20:00.",
    "preferred_groomer": "К какому мастеру хотите: Анна, Дарья, Марина или без предпочтений?",
    "comment": "Есть ли особенности характера, аллергия, кожные проблемы или сильный страх? Если нет, напишите «нет особенностей».",
}
LABELS = dict(zip(QUESTIONS, ["Владелец", "Телефон", "Питомец", "Порода", "Возраст", "Услуга", "Дата", "Время", "Мастер", "Комментарий"]))
CONFIRM = {"подтверждаю", "подтверждаю заявку", "отправить заявку", "да, подтверждаю"}
CANCEL = {"отменить заявку", "отмена заявки", "не отправлять", "отмена"}

@dataclass
class BookingState:
    draft: Draft = field(default_factory=Draft)
    active: bool = False
    pending: Application | None = None
    application_id: str | None = None
    saved: bool = False
    last_request: float = 0


def route_intent(message: str, extracted: Extraction) -> str:
    """Transparent Python routing; semantic classification is only a hint."""
    if re.search(r"запис|заявк|заброни", message.lower()):
        return "запись"
    if re.search(r"цен[ауы]|стоим|сколько.*(стоит|руб)|прайс", message.lower()):
        return "цена"
    if re.search(r"свободн|скидк|акци|есть.*мест", message.lower()):
        if "свободн" in message.lower() and "availability" not in extracted.topics:
            extracted.topics.append("availability")
        return "общее"
    return extracted.intent


def render_facts(extracted: Extraction, salon: dict) -> str:
    if extracted.medical:
        return salon["medical"]
    if extracted.intent == "цена":
        keys = extracted.price_services
        if keys:
            return "\n".join(f"{salon['services'][k]['name']}: {salon['services'][k]['price']}." for k in keys)
        return "Уточните нужную услугу, породу и размер питомца, а также состояние шерсти — есть ли колтуны?"
    parts = []
    for topic in extracted.topics[:4]:
        if topic in {"address", "hours", "payment", "cancellation", "included", "preparation"}:
            parts.append(salon[topic])
        elif topic == "contacts":
            parts.append(f"Телефон: {salon['phone']}. Telegram: {salon['telegram']}.")
        elif topic == "groomers":
            parts.append("\n".join(f"{k} — {v}." for k, v in salon["groomers"].items()))
        elif topic == "services":
            parts.append("\n".join(f"{v['name']}: {v['price']}." for v in salon["services"].values()))
        elif topic == "availability":
            parts.append("У меня нет данных о свободных местах. Могу принять заявку на желаемое время; это не подтверждение записи.")
        elif topic == "greeting":
            parts.append(f"Здравствуйте! Я ассистент салона «{salon['name']}». Подскажу услуги, цены и помогу оставить заявку.")
    return "\n\n".join(dict.fromkeys(parts)) or f"Таких сведений у меня нет. Уточните в салоне: {salon['phone']} или {salon['telegram']}."


def summary(application: Application, salon: dict) -> str:
    values = application.model_dump(mode="json")
    values["service"] = salon["services"][application.service]["name"]
    values["preferred_time"] = application.preferred_time.strftime("%H:%M")
    lines = [f"{LABELS[key]}: {html.escape(str(values[key]))}" for key in QUESTIONS]
    return ("Проверьте заявку:\n\n" + "\n\n".join(lines) +
            "\n\nЭто пожелание по времени, свободный слот не подтверждён. "
            "Чтобы отправить эти данные в салон (в том числе в Telegram) и сохранить в Supabase, напишите «Подтверждаю». "
            "Для исправления укажите, что изменить; для отмены — «Отменить заявку».")

class Assistant:
    def __init__(self, extractor, store, salon=None, notifier=None):
        self.extractor, self.store, self.salon = extractor, store, salon or SALON
        self.notifier = notifier

    def respond(self, message: str, history: list[dict], state: BookingState):
        message = message.strip()
        if not message:
            return history, state
        if len(message) > 4000:
            return self._reply(message[:4000], "Сократите сообщение до 4 000 символов, пожалуйста.", history, state)
        normalized = message.casefold().strip(" .!\n")
        if normalized in CANCEL:
            if state.saved:
                reply = "Заявка уже отправлена. Для отмены или переноса свяжитесь с салоном: " + self.salon["phone"] + ". " + self.salon["cancellation"]
            else:
                state = BookingState()
                reply = "Черновик заявки отменён. Чем ещё помочь?"
            return self._reply(message, reply, history, state)
        if normalized in CONFIRM:
            if state.saved:
                return self._reply(message, "Эта заявка уже сохранена. Повторно её не отправляю.", history, state)
            if not state.pending:
                return self._reply(message, "Пока нет готовой заявки для подтверждения. Напишите «Хочу записаться».", history, state)
            try:
                # Validate date again when confirming (a session may cross midnight).
                validated = Application.model_validate(state.pending.model_dump())
                self.store.save(state.application_id, validated, self.salon)
            except ValidationError:
                state.pending = None
                return self._reply(message, "Дата заявки уже прошла. Укажите новую дату.", history, state)
            except Exception:
                # Never put API exceptions, customer data or credentials into logs/UI.
                return self._reply(message, "Не удалось подтвердить сохранение заявки. Данные сохранены в текущем диалоге. Повторите «Подтверждаю» или свяжитесь с салоном: " + self.salon["phone"] + ".", history, state)
            state.saved, state.active = True, False
            if self.notifier is not None and self.notifier.enabled:
                try:
                    message_id = self.notifier.send(state.application_id, validated, self.salon)
                    logging.getLogger("uvicorn.error").info(
                        "telegram_delivery_confirmed application_id=%s message_id=%s",
                        state.application_id, message_id)
                except Exception:
                    # Saving succeeded. Never ask the client to create a second application.
                    logging.getLogger(__name__).warning(
                        "telegram_delivery_unconfirmed application_id=%s", state.application_id)
            return self._reply(message, "Заявка сохранена. Номер: " + state.application_id + ". Это ещё не подтверждение записи на выбранное время. Уточнить запись можно по телефону " + self.salon["phone"] + ".", history, state)
        # A short, session-local throttle protects accidental rapid clicks.
        if time.monotonic() - state.last_request < 1:
            return self._reply(message, "Подождите секунду и повторите сообщение, пожалуйста.", history, state)
        state.last_request = time.monotonic()
        try:
            extracted = self.extractor.extract(message, history, state.draft, self.salon)
        except Exception:
            return self._reply(message, "Сейчас не удаётся получить ответ AI-ассистента. Попробуйте ещё раз чуть позже. Связаться с салоном: " + self.salon["phone"] + ".", history, state)
        intent = route_intent(message, extracted)
        extracted.intent = intent
        if extracted.medical:
            return self._reply(message, self.salon["medical"], history, state)
        if state.saved and intent == "запись":
            state = BookingState(active=True)
        patch = {k: v for k, v in extracted.patch.model_dump().items() if v is not None and v != ""}
        changed = any(getattr(state.draft, k) != v for k, v in patch.items())
        if changed:
            state.draft = Draft.model_validate({**state.draft.model_dump(), **patch})
            if state.pending:
                state.pending = None
                state.application_id = None
        if intent == "запись":
            state.active = True
        if state.active and intent == "запись":
            missing = [key for key in QUESTIONS if not getattr(state.draft, key)]
            if missing:
                reply = QUESTIONS[missing[0]]
            else:
                try:
                    application = Application.model_validate(state.draft.model_dump())
                except ValidationError as exc:
                    error = exc.errors()[0]
                    key = error["loc"][0]
                    reply = "Проверьте данные. " + QUESTIONS.get(key, "Уточните данные заявки.")
                else:
                    state.pending = application
                    state.application_id = state.application_id or str(uuid4())
                    reply = summary(application, self.salon)
        else:
            reply = render_facts(extracted, self.salon)
        return self._reply(message, reply, history, state)

    @staticmethod
    def _reply(message, reply, history, state):
        # Escape client content before Gradio's Markdown renderer.
        history = [*history, {"role": "user", "content": html.escape(message)},
                   {"role": "assistant", "content": reply}][-80:]
        return history, state
