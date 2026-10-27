"""Readable, escaped HTML for the current application review; no submission logic."""
from datetime import datetime
from html import escape
from textwrap import shorten

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
    """Compact client review; administrator-only handoff and optional details stay in the application."""
    rows = []

    def row(label, value, *, wide=False):
        if value:
            css = ' class="review-wide"' if wide else ''
            rows.append(f'<div{css}><dt>{escape(label)}</dt><dd>{escape(str(value))}</dd></div>')

    service = salon['services'].get(application.service)
    row('Услуга', service['name'] if service else shorten(application.request_text or 'Уточнит администратор', width=160, placeholder='…'), wide=True)
    if service:
        row('Стоимость', service['price'])
    when = desired_when(application)
    if application.schedule_note and not (application.preferred_date and application.preferred_time):
        known = when.split(' · ')[0] if application.preferred_date or application.preferred_time else ''
        when = ' · '.join(value for value in (known, application.schedule_note) if value)
    row('Когда', when)
    row('Телефон', application.phone)
    pet = ' · '.join(value for value in (application.pet_name, application.pet_breed or application.pet_type) if value)
    row('Питомец', pet, wide=True)
    if application.preferred_groomer != 'Без предпочтений':
        row('Мастер', application.preferred_groomer)
    return ('<dl class="review-fields">' + ''.join(rows) + '</dl>'
            '<p class="review-note">Дата и время — пожелание. Запись не подтверждена.</p>')
