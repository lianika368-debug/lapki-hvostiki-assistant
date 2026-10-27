"""Conversation first; a validated, versioned application is sent only on consent."""
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4
import json
import logging
import re
import time
from datetime import datetime, date, time as clock_time
from pydantic import ValidationError
from models import Application, Draft, Extraction, TZ
from lapka import safe_text, INSTRUCTION_OVERRIDE, OFF_TOPIC, BOUNDARY_REPLIES
from conversation import (FIELD_LABELS, PET_FIELDS, DOG_SERVICES, DECLINE, CANCEL, BOOK,
                          lexical_topics, requests_price, validate_patch, explicit_patch,
                          reconcile_service, render_facts, explicit_conflicts, wants_booking)

SALON = json.loads(Path(__file__).with_name('salon.json').read_text())
# Only the user's task and a way to contact them are required for a preliminary request.
QUESTIONS = {'request_text': 'Что передать администратору: какую услугу хотите или что нужно питомцу?',
             'phone': 'Чтобы администратор мог связаться с вами по заявке, укажите телефон с кодом страны. Например, +7 (999) 123-45-67.'}
LABELS = FIELD_LABELS
CONFIRM = {'подтверждаю', 'подтверждаю заявку', 'отправить заявку', 'да, подтверждаю'}


@dataclass
class BookingState:
    draft: Draft = field(default_factory=Draft)
    active: bool = False
    pending: Application | None = None
    application_id: str | None = None
    revision: str | None = None
    saved: bool = False
    last_request: float = 0
    awaiting_field: str | None = None
    issues: dict[str, str] = field(default_factory=dict)
    explained: list[str] = field(default_factory=list)
    suggestion: str | None = None
    last_error: bool = False
    reset_requested: bool = False
    editing: bool = False
    delivery: str = 'not_attempted'
    form_visible: bool = False
    form_rendered: dict[str, str] = field(default_factory=dict)
    form_error: str = ''


def route_intent(message, extracted):
    if DECLINE.search(message):
        return 'цена' if requests_price(message) else 'общее'
    if requests_price(message):
        return 'цена'
    if wants_booking(message):
        return 'запись'
    if '?' in message and extracted.action == 'consult' and extracted.intent == 'запись':
        return 'общее'
    return extracted.intent


def unresolved(draft, explained):
    items = []
    if not draft.service:
        items.append('Уточнить услугу' + (' и категорию размера собаки' if draft.pet_type == 'собака' else ''))
    if not draft.preferred_date or not draft.preferred_time:
        items.append('Согласовать точные дату и время')
    if 'duration' in explained:
        items.append('Уточнить длительность процедуры')
    return items


def handoff(draft, salon, explained):
    # Summarise grounded facts already discussed, not model-written promises.
    topics = {'included': 'объяснён состав комплекса', 'preparation': 'объяснена подготовка',
              'duration': 'сообщено, что длительность уточняет администратор',
              'availability': 'сообщено, что свободный слот не подтверждён',
              'price': 'обсуждён ориентировочный прайс', 'recommendation': 'предложены варианты ухода; выбор остаётся за клиентом'}
    lines = ['Обсуждалось: ' + '; '.join(topics[k] for k in explained if k in topics)] if explained else []
    if draft.service:
        lines.append('Ориентировочная стоимость: ' + salon['services'][draft.service]['price'])
    lines.append('Осталось: ' + '; '.join(unresolved(draft, explained) or ['подтвердить возможность записи на желаемое время']))
    return '\n'.join(lines)[:2200]


def build_application(state, salon):
    return Application.model_validate({**state.draft.model_dump(), 'handoff': handoff(state.draft, salon, state.explained)})


def summary(application, salon):
    values = application.model_dump(mode='json')
    lines = []
    for key, label in FIELD_LABELS.items():
        value = values.get(key)
        if value is None:
            continue
        if key == 'service':
            value = salon['services'][value]['name']
        if key == 'preferred_time':
            value = application.preferred_time.strftime('%H:%M')
        lines.append(f'{label}: {value}')
    if application.service:
        lines.append('Ориентировочная цена: ' + salon['services'][application.service]['price'])
    else:
        lines.append('Услуга и цена: для уточнения администратором')
    if application.handoff:
        lines.extend(line for line in application.handoff.splitlines() if not line.startswith('Ориентировочная стоимость:'))
    return ('Проверьте заявку:\n\n' + '\n'.join(lines) +
            '\n\nПередадим эти данные и краткое резюме разговора администратору. '
            'Дата и время — пожелание, запись ещё не подтверждена. '
            'Телефон нужен для дальнейшей связи; способ и срок ответа пока не согласованы. '
            'Для отправки нажмите «Отправить заявку» или напишите «Подтверждаю». '
            'Можно изменить данные или отменить заявку.')


class Assistant:
    def __init__(self, extractor, store, salon=None, notifier=None, responder=None):
        self.extractor, self.store, self.salon = extractor, store, salon or SALON
        self.notifier = notifier
        self.responder = responder

    def _answer(self, message, reply, history, state, proposed_reply=None, require_prices=False):
        # Never rewrite a review, validation error, or persistence acknowledgement.
        if self.responder is None or state.last_error or state.issues or 'Проверьте заявку:' in reply:
            return self._reply(message, reply, history, state)
        next_field = (state.awaiting_field if state.active and
                      QUESTIONS.get(state.awaiting_field, '\0') in reply else None)
        missing = []
        if state.active:
            if not state.draft.service and not state.draft.request_text:
                missing.append('request_text')
            if not state.draft.phone:
                missing.append('phone')
        # Only facts already selected for this reply; no unrelated prices to guess from.
        facts = {'name': self.salon['name'], 'services': {
            key: value for key, value in self.salon['services'].items()
            if value['name'] in reply and value['price'] in reply}}
        for key in ('address', 'hours', 'phone', 'telegram', 'payment', 'cancellation', 'included', 'preparation'):
            if self.salon[key] in reply:
                facts[key] = self.salon[key]
        if any(name in reply for name in self.salon['groomers']):
            facts['groomers'] = self.salon['groomers']
        data = {
            'имя_ассистента': 'Лапка',
            'цена_запрошена': require_prices,
            'справочник_салона': facts,
            'ответ_приложения': reply,
            'сообщение_клиента': message,
            'заявка': {
                'статус': ('saved' if state.saved else 'awaiting_confirmation' if state.pending else
                           'collecting' if state.active else 'consultation'),
                'недостающие_поля': missing,
                'питомец_и_пожелания': state.draft.model_dump(exclude={'owner_name', 'phone'}),
                'время_подтверждено': False,
            },
            'следующее_поле': next_field,
            'следующий_вопрос': QUESTIONS.get(next_field),
            'предложенный_ответ': proposed_reply,
        }
        try:
            candidate = self.responder.compose(message, history, data)
            if candidate is None:
                pass
            elif safe_text(candidate, data):
                reply = candidate
            else:
                logging.getLogger(__name__).warning('lapka_response_fallback reason=validation')
        except Exception as exc:
            logging.getLogger(__name__).warning('lapka_response_fallback error_type=%s', type(exc).__name__)
        return self._reply(message, reply, history, state)

    def _invalidate(self, state):
        state.pending, state.revision = None, None
        # A failed insert retries the same UUID. Editing creates a new version.
        state.application_id = None

    def _prepare(self, state):
        if state.issues:
            state.pending, state.revision = None, None
            return next(iter(state.issues.values()))
        if not state.draft.service and not state.draft.request_text:
            state.awaiting_field = 'request_text'
            prefix = 'Указанное время — пожелание, его подтвердит администратор. ' if state.draft.preferred_date or state.draft.preferred_time or state.draft.schedule_note else ''
            return prefix + QUESTIONS['request_text']
        if not state.draft.phone:
            state.awaiting_field = 'phone'
            return QUESTIONS['phone']
        if state.draft.service and 'price' not in state.explained:
            state.explained.append('price')
        try:
            state.pending = build_application(state, self.salon)
        except ValidationError:
            state.pending, state.revision = None, None
            state.last_error = True
            return 'Проверьте данные заявки: телефон, дату и время. Можно оставить дату и время для согласования.'
        state.awaiting_field = None
        state.editing = False
        state.application_id = state.application_id or str(uuid4())
        state.revision = str(uuid4())
        return summary(state.pending, self.salon)

    def _confirm(self, message, history, state, revision):
        if state.saved:
            return self._reply(message, 'Эта заявка уже сохранена. Повторно её не отправляю.', history, state)
        if not state.pending or state.issues or state.editing:
            return self._reply(message, 'Сначала проверьте актуальную сводку заявки. Отправка пока недоступна.', history, state)
        if revision is not None and revision != state.revision:
            return self._reply(message, 'Это устаревшее подтверждение. Проверьте актуальную сводку и подтвердите её ещё раз.', history, state)
        try:
            current = build_application(state, self.salon)
            if current.model_dump() != state.pending.model_dump():
                self._invalidate(state)
                return self._reply(message, 'Данные изменились. ' + self._prepare(state), history, state)
            self.store.save(state.application_id, current, self.salon)
        except ValidationError:
            state.pending, state.revision = None, None
            state.issues['preferred_date'] = 'Дата заявки уже прошла или данные требуют проверки. Укажите новую дату или напишите «дату согласовать».'
            state.last_error = True
            return self._reply(message, state.issues['preferred_date'], history, state)
        except Exception as exc:
            state.last_error = True
            logging.getLogger(__name__).warning('application_save_failed application_id=%s error_type=%s', state.application_id, type(exc).__name__)
            return self._reply(message, 'Не удалось подтвердить сохранение заявки. Черновик остался в текущем диалоге. Повторите «Подтверждаю» или свяжитесь с салоном: ' + self.salon['phone'] + '.', history, state)
        state.saved, state.active = True, False
        if self.notifier is not None and self.notifier.enabled:
            try:
                message_id = self.notifier.send(state.application_id, current, self.salon)
                state.delivery = 'confirmed'
                logging.getLogger('uvicorn.error').info('telegram_delivery_confirmed application_id=%s message_id=%s', state.application_id, message_id)
            except Exception:
                state.delivery = 'unconfirmed'
                logging.getLogger(__name__).warning('telegram_delivery_unconfirmed application_id=%s', state.application_id)
        return self._reply(message, 'Заявка сохранена. Номер: ' + state.application_id + '. Конкретные услугу, цену и время необходимо согласовать с салоном. Связаться с салоном: ' + self.salon['phone'] + '.', history, state)

    def respond(self, message, history, state, revision=None):
        message = message.strip()
        state.last_error = False
        if not message:
            return history, state
        if len(message) > 4000:
            state.last_error = True
            return self._reply(message[:4000], 'Сократите сообщение до 4 000 символов, пожалуйста.', history, state)
        normalized = message.casefold().strip(' .!\n')
        if normalized in CONFIRM:
            return self._confirm(message, history, state, revision)
        if normalized in {'не подтверждаю', 'не подтверждаю заявку'}:
            return self._reply(message, 'Заявка не отправлена. Можно изменить данные или отменить черновик.', history, state)
        if CANCEL.fullmatch(message):
            if state.saved:
                reply = 'Заявка уже отправлена. Для отмены или переноса свяжитесь с салоном: ' + self.salon['phone'] + '. ' + self.salon['cancellation']
            else:
                state = BookingState()
                reply = 'Черновик заявки отменён. Чем помочь?'
            return self._reply(message, reply, history, state)
        if normalized in {'изменить заявку', 'изменить'}:
            state.pending, state.revision, state.editing = None, None, True
            return self._reply(message, 'Что изменить в заявке? Можно назвать несколько изменений одним сообщением.', history, state)
        if normalized in {'оставить прежнее значение', 'оставить как было'}:
            state.issues.clear()
            return self._reply(message, self._prepare(state) if state.active else 'Прежние значения сохранены.', history, state)
        early_boundary = ('instruction_override' if INSTRUCTION_OVERRIDE.search(message) else
                          'off_topic' if OFF_TOPIC.search(message) else None)
        if early_boundary:
            return self._reply(message, BOUNDARY_REPLIES[early_boundary], history, state)
        if time.monotonic() - state.last_request < 1:
            state.last_error = True
            return self._reply(message, 'Сообщение уже обрабатывалось. Подождите секунду и повторите при необходимости.', history, state)
        state.last_request = time.monotonic()
        try:
            missing = []
            if state.active:
                if not state.draft.service and not state.draft.request_text:
                    missing.append('request_text')
                if not state.draft.phone:
                    missing.append('phone')
            context = {'application_status': ('saved' if state.saved else 'awaiting_confirmation' if state.pending else
                                              'collecting' if state.active else 'consultation'),
                       'missing_fields':missing, 'next_field':state.awaiting_field if state.active else None,
                       'time_confirmed':False}
            extracted = self.extractor.extract(message, history, state.draft, self.salon, context=context)
        except Exception as exc:
            code = getattr(exc, 'code', None)
            logging.getLogger(__name__).warning('gemini_extract_failed error_type=%s code=%s', type(exc).__name__, code if isinstance(code, int) else 'unknown')
            state.last_error = True
            # An attempted edit must not leave an old summary sendable.
            if state.pending:
                state.pending, state.revision = None, None
                state.issues['edit'] = 'Не удалось разобрать последнее сообщение. Повторите его или выберите «Оставить как было».'
            return self._reply(message, 'Сейчас не удаётся получить ответ ассистента. Ваш ввод и черновик сохранены в этом диалоге. Повторите сообщение или свяжитесь с салоном: ' + self.salon['phone'] + '.', history, state)
        intent = route_intent(message, extracted)
        extracted.intent = intent
        boundary = ('instruction_override' if INSTRUCTION_OVERRIDE.search(message) else
                    'off_topic' if OFF_TOPIC.search(message) else extracted.boundary)
        if boundary in BOUNDARY_REPLIES:
            return self._reply(message, BOUNDARY_REPLIES[boundary], history, state)
        declined = bool(DECLINE.search(message) or extracted.action == 'decline')
        if extracted.action == 'cancel':
            state.active, state.pending, state.revision = False, None, None
            return self._reply(message, 'Подготовка заявки остановлена. Для отмены уже отправленной заявки свяжитесь с салоном.' if state.saved else 'Черновик заявки отменён. Чем помочь?', history, BookingState() if not state.saved else state)
        if state.saved and (extracted.action == 'edit' or re.search(r'измен|помен|исправ|перенес|перенёс', message, re.I)):
            return self._reply(message, 'Заявка уже передана администратору. Для изменения сообщите салону номер ' + str(state.application_id) + ': ' + self.salon['phone'] + ' или ' + self.salon['telegram'] + '. Сохранённые данные сейчас не изменялись.', history, state)
        if extracted.multiple_pets:
            self._invalidate(state)
            state.issues['multiple_pets'] = 'В одном запросе оформляем одного питомца. О каком питомце продолжим?'
            return self._reply(message, state.issues['multiple_pets'], history, state)
        if extracted.action == 'new_pet' or re.search(r'друг(?:ой|ого|ом)\s+питом|теперь\s+(?:о\s+)?(?:кошк|кот|собак)', message, re.I):
            for key in set(Draft.model_fields) - {'owner_name', 'phone'}:
                setattr(state.draft, key, None)
            state.issues.clear()
            self._invalidate(state)
        if state.saved and (extracted.action == 'book' or wants_booking(message)):
            state = BookingState(active=True, draft=Draft(owner_name=state.draft.owner_name, phone=state.draft.phone))
        if extracted.medical:
            return self._reply(message, self.salon['medical'], history, state)
        topics = list(dict.fromkeys([*extracted.topics, *lexical_topics(message)]))
        extracted.topics = topics
        if extracted.recommendation and (requests_price(message) or 'duration' in topics) and not re.search(r'не знаю|не понимаю|помог|посовет|выбрать|подой[дт]|нужна ли|нужен ли', message, re.I):
            extracted.recommendation = None
        if 'services' in topics and not re.search(r'все\s+услуг|какие.*услуг|прайс|перечень|список|услуги и цены', message, re.I):
            topics.remove('services')
        if extracted.recommendation and extracted.recommendation != 'complex' and 'included' in topics and 'included' not in lexical_topics(message):
            topics.remove('included')
        if 'duration' in topics and 'included' in topics and 'included' not in lexical_topics(message):
            topics.remove('included')
        if extracted.recommendation and not requests_price(message):
            extracted.price_services = []
            extracted.intent = intent = 'общее'
        if 'duration' in topics and not requests_price(message):
            # The model sometimes repeats price_services from a previous price question.
            extracted.price_services = []
            extracted.intent = intent = 'общее'
        if declined:
            state.active, state.pending, state.revision, state.awaiting_field = False, None, None, None
        patch = {k:v for k,v in extracted.patch.model_dump().items() if v not in (None, '')}
        size_words = [('мелкий', r'мелк|маленьк|небольш'), ('средний', r'средн'), ('крупный', r'крупн|большая собака|большой пёс')]
        explicit_size = next((size for size,pattern in size_words if re.search(pattern, message, re.I)), None)
        if '?' in message and re.search(r'это\s+(?:мелк|средн|крупн)|относится|считается|(?:мелк|средн|крупн)\w*\s+или', message, re.I):
            explicit_size = None
        if explicit_size:
            patch['pet_size'] = explicit_size
        else:
            patch.pop('pet_size', None)
        if extracted.recommendation and extracted.action == 'consult' and not re.search(r'выбираю|выбрал|хочу\s+(?:экспресс|стриж|комплекс)', message, re.I):
            patch.pop('service', None)
        patch.update(explicit_patch(message, state.awaiting_field))
        # Facts cannot be inferred from a question about an unrelated field.
        if '?' in message and extracted.action == 'consult':
            for key in ('owner_name', 'phone', 'comment'):
                if not re.search({'owner_name': r'меня зовут|имя владельца', 'phone': r'телефон|номер', 'comment': r'особенност|аллерги|боится'}[key], message, re.I):
                    patch.pop(key, None)
        if any(getattr(state.draft,k) != v for k,v in patch.items()) or extracted.clear_fields or extracted.conflicts:
            self._invalidate(state)
        for key in extracted.clear_fields:
            if key in Draft.model_fields:
                setattr(state.draft, key, None)
                state.issues.pop(key, None)
        scheduling = re.search(r'(дату(?: и время)?|время(?: и дату)?)\s+(?:пока\s+)?(?:согласовать|не знаю|не определ\w*)', message, re.I)
        if scheduling:
            targets = ([ 'preferred_date' ] if 'дату' in scheduling[1].lower() else []) + ([ 'preferred_time' ] if 'время' in scheduling[1].lower() else [])
            for key in targets:
                setattr(state.draft, key, None)
                state.issues.pop(key, None)
                patch.pop(key, None)
            state.draft.schedule_note = scheduling[0]
            self._invalidate(state)
        if any(k in patch for k in ('pet_breed', 'pet_type', 'pet_size', 'pet_weight')):
            if 'service' not in patch and state.draft.service in {*DOG_SERVICES.values(), 'cat_full'}:
                state.draft.request_text = state.draft.request_text or 'Комплексный груминг'
                state.draft.service = None
            if 'pet_size' not in patch and any(k in patch and getattr(state.draft,k) != patch[k] for k in ('pet_breed','pet_weight','pet_type')):
                state.draft.pet_size = None
        accepted, errors = validate_patch(patch)
        for key, value in accepted.items():
            setattr(state.draft, key, value)
            state.issues.pop(key, None)
        if accepted:
            state.issues.pop('edit', None)
            if any(k in accepted for k in PET_FIELDS):
                state.issues.pop('multiple_pets', None)
        state.issues.update(errors)
        if state.draft.preferred_date and state.draft.preferred_time:
            stamp = datetime.combine(date.fromisoformat(state.draft.preferred_date), clock_time.fromisoformat(state.draft.preferred_time), TZ)
            if stamp < datetime.now(TZ):
                state.issues['preferred_time'] = 'Указанные дата и время уже прошли в Екатеринбурге. Укажите будущий вариант или напишите «время согласовать».'
        state.issues.update(explicit_conflicts(message))
        for conflict in extracted.conflicts:
            if conflict.field in FIELD_LABELS:
                state.issues[conflict.field] = 'Нужно уточнение: ' + conflict.question
        # The exact malformed time must block even if the model chose an old valid value.
        state.draft = reconcile_service(state.draft)
        if extracted.recommendation:
            state.suggestion = extracted.recommendation
        explicit_booking = bool(wants_booking(message) or extracted.action == 'book')
        # Legacy extraction intent alone is never enough to override a refusal/question.
        if explicit_booking and not declined:
            state.active = True
            if not state.draft.request_text and not state.draft.service and re.search(r'комплекс|стриж|линя|линьк|колтун|груминг|мыть|когт|зуб|окраш', message, re.I):
                state.draft.request_text = message[:1800]
        if errors or extracted.conflicts or state.issues:
            state.pending, state.revision = None, None
            state.last_error = bool(errors)
            applied = [FIELD_LABELS[k] for k in accepted if k not in state.issues]
            prefix = ('Применено: ' + ', '.join(applied) + '.\n') if applied else ''
            return self._reply(message, prefix + next(iter(state.issues.values())) + '\nМожно исправить значение или выбрать «Оставить как было».', history, state)
        is_question = extracted.action == 'consult' and (bool(topics) or intent in {'цена','общее'} or extracted.recommendation)
        def note_explained():
            for topic in topics + (['price'] if intent == 'цена' or extracted.price_services else []) + (['recommendation'] if extracted.recommendation else []):
                if topic not in state.explained:
                    state.explained.append(topic)
        if declined or is_question and not explicit_booking:
            note_explained()
            reply = render_facts(extracted, self.salon, state.draft)
            if declined:
                reply = 'Подготовку заявки остановила.\n\n' + reply
            # Refresh current version without appending an irrelevant phone question.
            if state.active and state.draft.phone and (state.draft.service or state.draft.request_text):
                self._prepare(state)
            return self._answer(message, reply, history, state, proposed_reply=extracted.reply, require_prices=extracted.intent == 'цена')
        if state.active:
            if explicit_booking and (requests_price(message) or lexical_topics(message)):
                note_explained()
                reply = render_facts(extracted, self.salon, state.draft) + '\n\n' + self._prepare(state)
            else:
                reply = self._prepare(state)
        elif accepted:
            if topics or extracted.recommendation:
                note_explained()
            reply = 'Учла данные питомца. ' + (render_facts(extracted, self.salon, state.draft) if topics or extracted.recommendation else 'Что хотите уточнить об уходе или услуге?')
        else:
            note_explained()
            reply = render_facts(extracted, self.salon, state.draft)
        return self._answer(message, reply, history, state, proposed_reply=extracted.reply, require_prices=extracted.intent == 'цена')

    @staticmethod
    def _reply(message, reply, history, state):
        # Chat is plain text with sanitization, so no HTML entity escaping is needed.
        history = [*history, {'role':'user','content':message}, {'role':'assistant','content':reply}][-80:]
        return history, state
