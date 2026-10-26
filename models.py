"""Typed conversation data. Unknown optional details remain null, never invented."""
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo
import re
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

TZ = ZoneInfo('Asia/Yekaterinburg')
ServiceId = Literal['shedding', 'hygiene', 'cat_full', 'dog_small', 'dog_medium', 'dog_large', 'nails', 'ears', 'teeth', 'mats', 'color']
Topic = Literal['address', 'hours', 'contacts', 'payment', 'cancellation', 'included', 'groomers', 'preparation', 'services', 'availability', 'duration', 'unknown', 'greeting', 'capabilities']


def normalize_phone(value):
    value = re.sub(r'[\s()\-]', '', value)
    if value.startswith('8') and len(value) == 11:
        value = '+7' + value[1:]
    if not re.fullmatch(r'\+[1-9]\d{9,14}', value):
        raise ValueError('Нужен телефон с кодом страны, например +7 (999) 123-45-67. Номер «123» слишком короткий.')
    if len(set(value[-10:])) == 1:
        raise ValueError('Номер похож на заглушку: все последние десять цифр одинаковые. Укажите действующий телефон для связи.')
    return value


class Draft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    owner_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    pet_name: str | None = Field(default=None, max_length=100)
    pet_breed: str | None = Field(default=None, max_length=150)
    pet_age: str | None = Field(default=None, max_length=80)
    pet_type: Literal['кошка', 'собака', 'другое'] | None = None
    pet_weight: str | None = Field(default=None, max_length=80)
    pet_size: Literal['мелкий', 'средний', 'крупный'] | None = None
    service: ServiceId | None = None
    request_text: str | None = Field(default=None, max_length=1800)
    preferred_date: str | None = Field(default=None, max_length=30)
    preferred_time: str | None = Field(default=None, max_length=30)
    schedule_note: str | None = Field(default=None, max_length=300)
    preferred_groomer: Literal['Анна', 'Дарья', 'Марина', 'Без предпочтений'] | None = None
    comment: str | None = Field(default=None, max_length=1000)


class Conflict(BaseModel):
    field: str
    question: str = Field(max_length=250)


class Extraction(BaseModel):
    _api_attempts: int = PrivateAttr(default=0)
    model_config = ConfigDict(extra='forbid')
    intent: Literal['цена', 'запись', 'общее']
    action: Literal['consult', 'book', 'continue', 'edit', 'decline', 'cancel', 'new_pet'] = 'consult'
    medical: bool = False
    topics: list[Topic] = Field(default_factory=list, max_length=8)
    price_services: list[ServiceId] = Field(default_factory=list, max_length=11)
    recommendation: Literal['shedding', 'mats', 'hygiene', 'complex'] | None = None
    patch: Draft = Field(default_factory=Draft)
    clear_fields: list[str] = Field(default_factory=list, max_length=16)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=6)
    multiple_pets: bool = False
    boundary: Literal['none', 'instruction_override', 'off_topic'] = 'none'
    reply: str | None = Field(default=None, max_length=1800)


class Application(Draft):
    phone: str
    preferred_date: date | None = None
    preferred_time: time | None = None
    handoff: str | None = Field(default=None, max_length=2200)

    @field_validator('phone')
    @classmethod
    def phone_valid(cls, value):
        return normalize_phone(value)

    @field_validator('preferred_date')
    @classmethod
    def date_valid(cls, value):
        if value is not None and value < datetime.now(TZ).date():
            raise ValueError('Эта дата уже прошла. Назовите будущую дату или оставьте её для согласования.')
        return value

    @field_validator('preferred_time')
    @classmethod
    def time_valid(cls, value):
        if value is not None and (value.tzinfo or value.second or value.microsecond or not time(10) <= value <= time(20)):
            raise ValueError('Время должно быть с 10:00 до 20:00. Можно указать пожелание, например «после обеда».')
        return value

    @model_validator(mode='after')
    def request_required(self):
        if not self.service and not self.request_text:
            raise ValueError('Нужна выбранная услуга или описание задачи для администратора.')
        if self.preferred_date and self.preferred_time and datetime.combine(self.preferred_date, self.preferred_time, TZ) < datetime.now(TZ):
            raise ValueError('Указанные дата и время уже прошли в Екатеринбурге. Укажите будущий вариант или оставьте время для согласования.')
        return self
