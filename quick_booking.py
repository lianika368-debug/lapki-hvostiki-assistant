"""Fast booking form: validated input and explicit start phrases, without an AI call."""
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from models import normalize_phone
from conversation import DECLINE, requests_price, explicit_patch, explicit_conflicts, validate_patch
from lapka import INSTRUCTION_OVERRIDE, OFF_TOPIC

BATCH_PROMPT = ('Для заявки достаточно телефона и услуги или описания задачи. '
                'Заполните их вместе в форме ниже; пожелания можно добавить по желанию.')


def _start_text(message):
    text = re.sub(r'\s+', ' ', message.casefold().replace('ё', 'е')).strip(' .!?\n')
    return re.sub(r'^(?:привет|здравствуйте|добрый день)[,! ]+', '', text)


PLAIN_STARTS = {'хочу записаться', 'я хочу записаться', 'запишите меня', 'запишите нас',
                'запиши меня', 'записаться', 'можно записаться', 'хочу записать питомца',
                'хочу оставить заявку', 'подготовить заявку', 'нужна запись', 'давайте запишемся'}


def is_booking_start(message):
    text = _start_text(message)
    if text in PLAIN_STARTS:
        return True
    # Only explicit booking intent. Questions about prices/health retain the AI consultation path.
    if len(message) > 1800 or DECLINE.search(message) or requests_price(message) or explicit_conflicts(message):
        return False
    if re.search(r'\b(?:передумал\w*|не\s+запис\w*)', text):
        return False
    if INSTRUCTION_OVERRIDE.search(message) or OFF_TOPIC.search(message):
        return False
    if re.search(r'\b(?:зуд|болит|боль|кров|ран[аыу]|рвот|сыпь|леч|температур|диагноз|отрав|судорог|лекарств|гно[йя]|лиша[йя])', text):
        return False
    return bool(re.search(r'\b(?:хочу\s+записат|запишите|запиши\s|можно\s+записаться|хочу\s+оставить\s+заявку)', text))


def booking_details(message, draft, salon):
    """Seed form controls without guessing: retain the entire detailed request as task or note."""
    if _start_text(message) in PLAIN_STARTS:
        return {}
    raw = message.strip()
    values = form_values(draft, salon)
    parsed = explicit_patch(raw)
    if 'phone' not in parsed:
        phone = re.search(r'(?<!\w)(?:\+7|8)[\d ()-]{8,25}\d', raw)
        if phone:
            parsed['phone'] = phone[0].strip()
    normalized_raw = raw.casefold().replace('ё', 'е')
    matched = []
    for item in salon['services'].values():
        name = item['name'].casefold().replace('ё', 'е')
        accusative = name.replace('гигиеническая', 'гигиеническую').replace('стрижка', 'стрижку').replace('чистка', 'чистку').replace('линька', 'линьку')
        if any(value in normalized_raw for value in (name, accusative)):
            matched.append(item['name'])
    notes = '\n'.join(value for value in (values['note'], raw) if value)
    if len(matched) == 1 and len(notes) <= 1000 and not re.search(r'\bне\b', raw, re.I):
        values['task'], values['note'] = matched[0], notes
    else:
        values['task'] = raw
    if parsed.get('phone'):
        values['phone'] = parsed['phone']
    if parsed.get('preferred_date') or parsed.get('preferred_time'):
        values['when'] = ' '.join(value for value in (
            '.'.join(reversed(parsed['preferred_date'].split('-'))) if parsed.get('preferred_date') else '',
            parsed.get('preferred_time', '')) if value)
    return values


class QuickBooking(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    phone: str = Field(min_length=1, max_length=30)
    task: str = Field(min_length=1, max_length=1800)
    when: str = Field(default='', max_length=300)
    note: str = Field(default='', max_length=1000)

    @field_validator('phone')
    @classmethod
    def valid_phone(cls, value):
        return normalize_phone(value)


def form_values(draft, salon):
    service = salon['services'].get(draft.service)
    when = draft.schedule_note or ' '.join(value for value in (
        '.'.join(reversed(draft.preferred_date.split('-'))) if draft.preferred_date else '',
        draft.preferred_time or '') if value)
    return {'phone': draft.phone or '', 'task': service['name'] if service else draft.request_text or '',
            'when': when, 'note': draft.comment or ''}


def form_patch(data, draft, salon):
    old = form_values(draft, salon)
    patch = {'phone': data.phone, 'comment': data.note or None}
    if data.task != old['task']:
        patch['service'] = next((key for key, item in salon['services'].items()
                                 if item['name'].casefold() == data.task.casefold()), None)
        patch['request_text'] = data.task
    if data.when != old['when']:
        parsed, errors = validate_patch(explicit_patch(data.when))
        if errors:
            raise ValueError(next(iter(errors.values())))
        patch.update(preferred_date=None, preferred_time=None, schedule_note=data.when or None)
        patch.update(parsed)
    return patch


def form_error(exc):
    if isinstance(exc, ValueError) and not hasattr(exc, 'errors'):
        return str(exc)
    messages = []
    for error in exc.errors():
        key = error['loc'][0]
        if key == 'phone':
            messages.append('Проверьте телефон: нужен номер с кодом страны, например +7 (999) 123-45-67.')
        elif key == 'task':
            messages.append('Укажите услугу или коротко опишите задачу для питомца (до 1 800 символов).')
        elif key == 'when':
            messages.append('Сократите пожелание по времени до 300 символов.')
        else:
            messages.append('Сократите комментарий до 1 000 символов.')
    return ' '.join(dict.fromkeys(messages))
