"""Grounded service selection and immediate validation, independent of the model."""
from datetime import date, datetime, time, timedelta
import re
from models import Draft, Extraction, TZ, normalize_phone

DOG_SERVICES = {'мелкий': 'dog_small', 'средний': 'dog_medium', 'крупный': 'dog_large'}
FIELD_LABELS = {
    'owner_name': 'Владелец', 'phone': 'Телефон', 'pet_name': 'Питомец', 'pet_breed': 'Порода',
    'pet_age': 'Возраст', 'pet_type': 'Вид', 'pet_weight': 'Вес', 'pet_size': 'Размер',
    'service': 'Услуга', 'request_text': 'Запрос', 'preferred_date': 'Желаемая дата',
    'preferred_time': 'Желаемое время', 'schedule_note': 'Пожелание по времени',
    'preferred_groomer': 'Мастер', 'comment': 'Особенности',
}
PET_FIELDS = {'pet_name', 'pet_breed', 'pet_age', 'pet_type', 'pet_weight', 'pet_size',
              'service', 'request_text', 'comment'}
DECLINE = re.compile(r'(не\s+(?:хочу|буду|надо|нужно|собираюсь)\s+(?:пока\s+)?(?:запис|заяв|оформ)|только\s+(?:узнать|цен|спросить|консультац)|без\s+записи)', re.I)
CANCEL = re.compile(r'^(?:пожалуйста[, ]+)?(?:отмен[аи]|отменить\s+заявку|отмени(?:те)?\s+(?:эту\s+)?заявку|отмена заявки|не отправляй(?:те)?(?: заявку)?)[.!\s]*$', re.I)
BOOK = re.compile(r'\b(?:хочу записаться|запишите(?: меня| нас| питомца)?|давайте запиш|отправ(?:ьте|им) заявку|оформ(?:ите|им) заявку|хочу оставить заявку)', re.I)


def wants_booking(message):
    if BOOK.search(message):
        return True
    return bool('?' not in message and re.search(
        r'\b(?:нужен|нужна|нужно|нужны)\b.{0,80}(?:комплекс|стрижк|груминг|линьк|когт).{0,100}'
        r'(?:завтра|сегодня|после обеда|\bв\s+(?:субботу|воскресенье|понедельник|вторник|среду|четверг|пятницу)|\d{1,2}:\d{2})', message, re.I))


def lexical_topics(message):
    patterns = {'duration': r'длит|долго|сколько.*(?:времени|час|минут)|по времени',
                'included': r'что вход|состав|включен', 'preparation': r'подготов',
                'hours': r'график|часы работы|когда.*работа', 'address': r'адрес|где.*наход',
                'availability': r'свободн|есть.*мест', 'payment': r'оплат|сбп',
                'cancellation': r'правила отмен|перенос.*запис'}
    topics = [key for key, pattern in patterns.items() if re.search(pattern, message, re.I)]
    # Waiting for the chat response is not the duration of a grooming procedure.
    if 'duration' in topics and re.search(r'отвеч|ответ|ожидани|жду|тормоз', message, re.I) and not re.search(r'процедур|груминг|стрижк|мыть|сушк', message, re.I):
        topics.remove('duration')
    return topics


def requests_price(message):
    return bool(re.search(r'цен[ауы]|стоим|сколько.*стоит|прайс', message, re.I))


def validate_patch(patch):
    """Accept good fields in a multi-field edit, retain old values for rejected ones."""
    accepted, errors = {}, {}
    for field, value in patch.items():
        if value is None or value == '':
            continue
        try:
            if field == 'phone':
                value = normalize_phone(value)
            elif field == 'preferred_date':
                value = date.fromisoformat(value).isoformat()
                if date.fromisoformat(value) < datetime.now(TZ).date():
                    raise ValueError('Дата уже прошла. Укажите будущую дату или напишите «дату согласовать».')
            elif field == 'preferred_time':
                parsed = time.fromisoformat(value)
                if parsed.tzinfo or parsed.second or parsed.microsecond or not time(10) <= parsed <= time(20):
                    raise ValueError('Укажите время с 10:00 до 20:00 или пожелание вроде «после обеда».')
                value = parsed.strftime('%H:%M')
            Draft.model_validate({field: value})
            accepted[field] = value
        except Exception as exc:
            if field == 'phone':
                reason = str(exc) if isinstance(exc, ValueError) else 'Проверьте телефон с кодом страны.'
            elif field == 'preferred_date':
                reason = 'Проверьте дату: нужен существующий день сегодня или позднее. Можно написать «дату согласовать».'
            elif field == 'preferred_time':
                reason = 'Такого времени нет в графике салона. Укажите время с 10:00 до 20:00 или напишите «время согласовать».'
            else:
                reason = 'Значение слишком длинное или не соответствует этому полю. Уточните его, пожалуйста.'
            errors[field] = f'{FIELD_LABELS[field]} «{value}» не принято. {reason}'
    return accepted, errors


def explicit_patch(message, awaiting=None):
    """Keep malformed edits visible even if the model omitted them."""
    patch = {}
    times = re.findall(r'\b\d{1,2}:\d{2}\b', message)
    if len(set(times)) == 1 or times and re.search(r'помен|измен|исправ|вместо|перенес|перенёс', message, re.I):
        patch['preferred_time'] = times[-1]
    phone = re.search(r'(?:телефон|номер(?: телефона)?)\s*[:—-]?\s*([+\d][\d ()-]{1,28})', message, re.I)
    if phone:
        patch['phone'] = phone[1].strip()
    elif awaiting == 'phone' and re.fullmatch(r'[+\d\s()\-]+', message):
        patch['phone'] = message.strip()
    dates = re.findall(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b', message)
    if len(set(dates)) == 1:
        day, month, year = dates[0]
        patch['preferred_date'] = f'{year}-{int(month):02}-{int(day):02}'
    if re.search(r'\bзавтра\b', message, re.I) and not dates:
        patch['preferred_date'] = (datetime.now(TZ).date()+timedelta(days=1)).isoformat()
    elif re.search(r'\bсегодня\b', message, re.I) and not dates:
        patch['preferred_date'] = datetime.now(TZ).date().isoformat()
    elif not dates:
        weekdays = ('понедельник','вторник','среду','четверг','пятницу','субботу','воскресенье')
        for index, name in enumerate(weekdays):
            if re.search(r'\b(?:в|на)\s+(?:ближайш\w+\s+)?' + name + r'\b', message, re.I):
                today = datetime.now(TZ).date()
                days = (index-today.weekday()) % 7
                if re.search(r'следующ', message, re.I):
                    days = days + 7 if days == 0 else days
                patch['preferred_date'] = (today+timedelta(days=days)).isoformat()
                break
    return patch


def explicit_conflicts(message):
    if re.search(r'помен|измен|исправ|вместо|перенес|перенёс', message, re.I):
        return {}
    times = list(dict.fromkeys(re.findall(r'\b\d{1,2}:\d{2}\b', message)))
    if len(times) > 1:
        return {'preferred_time':'Какое время указать: ' + ' или '.join(times) + '?'}
    return {}


def reconcile_service(draft):
    """No undocumented breed/weight thresholds; a single mapping for all paths."""
    if draft.pet_type is None and draft.pet_breed:
        if re.search(r'кошка|кошк|\bкот\b', draft.pet_breed, re.I):
            draft.pet_type = 'кошка'
        elif re.search(r'собак|корги|пудел|шпиц|мальтипу', draft.pet_breed, re.I):
            draft.pet_type = 'собака'
    if draft.service in DOG_SERVICES.values():
        if draft.pet_type == 'кошка':
            draft.service = None
        else:
            draft.service = DOG_SERVICES.get(draft.pet_size)
    if draft.service == 'cat_full' and draft.pet_type == 'собака':
        draft.service = None
    if not draft.service and draft.request_text and re.search(r'комплекс', draft.request_text, re.I):
        if draft.pet_type == 'кошка':
            draft.service = 'cat_full'
        elif draft.pet_type == 'собака' and draft.pet_size:
            draft.service = DOG_SERVICES[draft.pet_size]
    return draft


def price_keys(extracted, draft):
    keys = list(extracted.price_services)
    if not keys and draft.service:
        keys = [draft.service]
    if not keys and draft.request_text and re.search(r'комплекс', draft.request_text, re.I):
        if draft.pet_type == 'собака':
            keys = list(DOG_SERVICES.values())
        elif draft.pet_type == 'кошка':
            keys = ['cat_full']
        else:
            keys = ['cat_full', *DOG_SERVICES.values()]
    if any(k in {'cat_full', *DOG_SERVICES.values()} for k in keys):
        if draft.pet_type == 'кошка':
            keys = [k for k in keys if k not in DOG_SERVICES.values()]
            keys = list(dict.fromkeys([*keys, 'cat_full']))
        elif draft.pet_type == 'собака' and 'cat_full' in keys:
            keys = [k for k in keys if k != 'cat_full'] + list(DOG_SERVICES.values())
    # Same pet data must resolve identically, irrespective of the model's guess.
    if any(k in DOG_SERVICES.values() for k in keys):
        keys = [k for k in keys if k not in DOG_SERVICES.values()]
        if draft.pet_type != 'кошка':
            keys += [DOG_SERVICES[draft.pet_size]] if draft.pet_size else list(DOG_SERVICES.values())
    return list(dict.fromkeys(keys))


def render_facts(extracted, salon, draft=None):
    draft = draft or Draft()
    if extracted.medical:
        return salon['medical']
    parts = []
    priced = []
    if extracted.intent == 'цена' or extracted.price_services:
        keys = price_keys(extracted, draft)
        priced = keys
        parts.append('\n'.join(f"{salon['services'][k]['name']}: {salon['services'][k]['price']}." for k in keys)
                     if keys else 'Если пока не выбрали услугу, помогу разобраться. Какой уход нужен вашему питомцу?')
        if any(k in DOG_SERVICES.values() for k in keys) and not draft.pet_size:
            parts.append('В прайсе есть мелкие, средние и крупные собаки, но границы по весу не указаны. '
                         'По породе и килограммам я не могу надёжно выбрать категорию — её уточнит администратор.')
    recommendation = extracted.recommendation
    if recommendation:
        options = {
            'shedding': ('shedding', 'Если основная задача — линька, можно рассмотреть экспресс-линьку. '
                         'Нужна ли ещё стрижка, уточнит мастер: правил по типам шерсти в справочнике нет.'),
            'mats': ('mats', 'В салоне есть расчёсывание колтунов. Точная стоимость зависит от состояния шерсти.'),
            'hygiene': ('hygiene', 'Можно рассмотреть гигиеническую стрижку. Детали её состава уточнит администратор.'),
        }
        if recommendation in options:
            key, reason = options[recommendation]
            if recommendation == 'mats':
                # The price already explains the dependency on the coat's condition.
                if key not in priced:
                    parts.append(f"{salon['services'][key]['name']}: {salon['services'][key]['price']}.")
            else:
                parts.append(reason + (f"\n{salon['services'][key]['name']}: {salon['services'][key]['price']}." if key not in priced else ''))
        elif recommendation == 'complex':
            parts.append(salon['included'])
    for topic in extracted.topics:
        if topic in {'address', 'hours', 'payment', 'cancellation', 'included', 'preparation'}:
            parts.append(salon[topic])
        elif topic == 'duration':
            parts.append('Длительность процедур в данных салона не указана. Её уточнит администратор с учётом питомца и услуги.')
        elif topic == 'contacts':
            parts.append(f"Телефон: {salon['phone']}. Telegram: {salon['telegram']}.")
        elif topic == 'groomers':
            parts.append('\n'.join(f'{k} — {v}.' for k,v in salon['groomers'].items()))
        elif topic == 'services':
            parts.append('\n'.join(f"{v['name']}: {v['price']}." for v in salon['services'].values()))
        elif topic == 'availability':
            parts.append('Данных о свободных местах нет. Можно передать пожелание по времени — конкретный вариант подтвердит администратор.')
        elif topic == 'capabilities':
            parts.append('Я Лапка, AI-ассистент салона «Лапки & Хвостики». Помогу узнать услуги и ориентировочные цены, подготовиться к визиту и составить тестовую заявку. Отправлю её только после вашего подтверждения; запись на конкретное время согласует администратор.')
        elif topic == 'greeting':
            parts.append('Здравствуйте! Помогу выбрать услугу, узнать стоимость и подготовить заявку. Что нужно вашему питомцу?')
    return '\n\n'.join(dict.fromkeys(parts)) or f"Таких сведений у меня нет. Уточните в салоне: {salon['phone']} или {salon['telegram']}."
