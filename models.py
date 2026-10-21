"""Validated business data; no model output can trigger a database write."""
from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

TZ = ZoneInfo("Asia/Yekaterinburg")
ServiceId = Literal["shedding", "hygiene", "cat_full", "dog_small", "dog_medium", "dog_large", "nails", "ears", "teeth", "mats", "color"]
Topic = Literal["address", "hours", "contacts", "payment", "cancellation", "included", "groomers", "preparation", "services", "availability", "unknown", "greeting"]

class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    owner_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    pet_name: str | None = Field(default=None, max_length=100)
    pet_breed: str | None = Field(default=None, max_length=150)
    pet_age: str | None = Field(default=None, max_length=80)
    service: ServiceId | None = None
    preferred_date: str | None = Field(default=None, max_length=10)
    preferred_time: str | None = Field(default=None, max_length=5)
    preferred_groomer: Literal["Анна", "Дарья", "Марина", "Без предпочтений"] | None = None
    comment: str | None = Field(default=None, max_length=1000)

class Extraction(BaseModel):
    intent: Literal["цена", "запись", "общее"]
    medical: bool = False
    topics: list[Topic] = Field(default_factory=list, max_length=4)
    price_services: list[ServiceId] = Field(default_factory=list, max_length=11)
    patch: Draft = Field(default_factory=Draft)

class Application(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    owner_name: str = Field(min_length=1, max_length=100)
    phone: str
    pet_name: str = Field(min_length=1, max_length=100)
    pet_breed: str = Field(min_length=1, max_length=150)
    pet_age: str = Field(min_length=1, max_length=80)
    service: ServiceId
    preferred_date: date
    preferred_time: time
    preferred_groomer: Literal["Анна", "Дарья", "Марина", "Без предпочтений"]
    comment: str = Field(min_length=1, max_length=1000)

    @field_validator("phone")
    @classmethod
    def phone_valid(cls, value):
        value = re.sub(r"[\s()\-]", "", value)
        if value.startswith("8") and len(value) == 11:
            value = "+7" + value[1:]
        if not re.fullmatch(r"\+[1-9]\d{9,14}", value):
            raise ValueError("Укажите телефон с кодом страны, например +7 (999) 123-45-67")
        return value

    @field_validator("preferred_date")
    @classmethod
    def date_valid(cls, value):
        if value < datetime.now(TZ).date():
            raise ValueError("Укажите желаемую дату сегодня или позднее")
        return value

    @field_validator("preferred_time")
    @classmethod
    def time_valid(cls, value):
        if value.tzinfo or value.second or value.microsecond or not time(10) <= value <= time(20):
            raise ValueError("Укажите желаемое время с 10:00 до 20:00")
        return value
