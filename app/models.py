"""Request bodies (pydantic)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

HANDLE_RE = r"^[A-Za-z0-9._-]{2,30}$"


class Signup(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field(pattern=HANDLE_RE)

    @field_validator("email")
    @classmethod
    def email_shape(cls, v: str) -> str:
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Enter a valid email address.")
        return v


class Login(BaseModel):
    email: str
    password: str


class SettingsPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    slots: int | None = Field(default=None, ge=1, le=30)
    intake_mode: Literal["auto", "open", "closed"] | None = None
    revisions_included: int | None = Field(default=None, ge=0, le=10)
    deposit_pct: int | None = Field(default=None, ge=0, le=100)
    hours_per_week: float | None = Field(default=None, gt=0, le=100)
    intake_message: str | None = Field(default=None, max_length=1000)


class CommissionTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    base_price: float = Field(ge=0, le=100000, description="Dollars")
    est_hours: float = Field(default=3, gt=0, le=500)
    characters_included: int = Field(default=1, ge=1, le=20)
    extra_character_pct: int = Field(default=60, ge=0, le=500)


class CommissionTypePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    base_price: float | None = Field(default=None, ge=0, le=100000)
    est_hours: float | None = Field(default=None, gt=0, le=500)
    characters_included: int | None = Field(default=None, ge=1, le=20)
    extra_character_pct: int | None = Field(default=None, ge=0, le=500)
    active: bool | None = None


class Brief(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    characters: int = Field(default=1, ge=1, le=20)
    background: Literal["None", "Simple", "Detailed"] = "None"
    references: list[str] = Field(default_factory=list, max_length=10)
    usage: Literal["Personal", "Commercial"] = "Personal"
    deadline: str = Field(default="", max_length=100)

    @field_validator("references")
    @classmethod
    def clean_refs(cls, v: list[str]) -> list[str]:
        return [r.strip()[:300] for r in v if r and r.strip()]


class ScreenIn(BaseModel):
    type_id: int
    brief: Brief


class IntakeIn(BaseModel):
    client_handle: str = Field(pattern=r"^@?[A-Za-z0-9._-]{2,40}$")
    email: str | None = Field(default=None, max_length=200)
    type_id: int
    brief: Brief


class AcceptIn(BaseModel):
    price: float | None = Field(default=None, ge=0, le=100000, description="Override price in dollars")


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=1000)


class HoursIn(BaseModel):
    hours: float = Field(gt=0, le=200)


class ChangeIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)


class PayIn(BaseModel):
    kind: Literal["deposit", "balance"]


class GoalIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000, description="Dollars")
    weeks: int = Field(ge=1, le=52)
    hours_per_week: float | None = Field(default=None, gt=0, le=100)
    narrate: bool = True
