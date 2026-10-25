"""Readable, escaped HTML for the current application review; no submission logic."""
from datetime import datetime
from html import escape

from models import TZ


MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')


def desired_when(application):
    day, clock = application.preferred_date, application.preferred_time
    if day:
        value = f'{day.day} {MONTHS[day.month - 1]}'
        if day.year != datetime.now(TZ).year:
            value += f' {day.year}'
        return value + (f', {clock:%H:%M}' if clock else ' · время согласовать')
    if clock:
        return f'{clock:%H:%M} · дату согласовать'
    return 'Дату и время согласовать'


def render_summary(application, salon):
    """Show only supplied values; keep the plain-text handoff unchanged."""
    rows = []

    def row(label, value, *, wide=False):
        if value:
            css = ' class="review-wide"' if wide else ''
            rows.append(f'<div{css}><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>')

    service = salon['services'].get(application.service)
    row('Услуга', service['name'] if service else 'Уточнит администратор', wide=True)
    row('Запрос', application.request_text, wide=True)
    row('Стоимость', service['price'] if service else 'Уточнит администратор')
    row('Когда', desired_when(application))
    row('Пожелание по времени', application.schedule_note, wide=True)
    row('Телефон', application.phone)
    row('Владелец', application.owner_name)
    pet = ' · '.join(value for value in (application.pet_name, application.pet_type, application.pet_breed) if value)
    row('Питомец', pet, wide=True)
    for label, key in (('Возраст', 'pet_age'), ('Вес', 'pet_weight'),
                       ('Размер', 'pet_size'), ('Мастер', 'preferred_groomer')):
        row(label, getattr(application, key))
    row('Комментарий', application.comment, wide=True)
    for line in (application.handoff or '').splitlines():
        if line.startswith('Обсуждалось: '):
            row('Обсудили', line.removeprefix('Обсуждалось: '), wide=True)
        elif line.startswith('Осталось: '):
            row('Уточнить', line.removeprefix('Осталось: '), wide=True)
    return ('<dl class="review-fields">' + ''.join(rows) + '</dl>'
            '<p class="review-note">Дата и время — пожелание. Запись не подтверждена.</p>'
            '<p class="review-hint">После подтверждения передадим тестовые данные и резюме разговора администратору.</p>')
