"""Pydantic models for bulletin extraction - single source of truth."""

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class DayOfWeek(str, Enum):
    SUNDAY = "Sunday"
    MONDAY = "Monday"
    TUESDAY = "Tuesday"
    WEDNESDAY = "Wednesday"
    THURSDAY = "Thursday"
    FRIDAY = "Friday"
    SATURDAY = "Saturday"


class EventFrequency(str, Enum):
    ONE_TIME = "one_time"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    FIRST_FRIDAY = "first_friday"
    OTHER_RECURRING = "other_recurring"


class MassTime(BaseModel):
    """A single scheduled Mass time."""

    day: DayOfWeek
    time: int = Field(
        ..., ge=0, le=2359, description="24hr format, e.g., 1630 for 4:30pm"
    )
    language: Optional[str] = Field(
        None, description="e.g., 'Spanish', 'Latin' if specified"
    )
    notes: Optional[str] = Field(None, description="e.g., 'First Friday only'")

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v: int) -> int:
        minutes = v % 100
        if minutes > 59:
            raise ValueError(f"Invalid time {v}: minutes must be 0-59")
        return v


class ConfessionTime(BaseModel):
    """A single scheduled confession time."""

    day: DayOfWeek
    start_time: int = Field(..., ge=0, le=2359, description="24hr format")
    end_time: int = Field(..., ge=0, le=2359, description="24hr format")
    notes: Optional[str] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: int) -> int:
        minutes = v % 100
        if minutes > 59:
            raise ValueError(f"Invalid time {v}: minutes must be 0-59")
        return v


class AdorationTime(BaseModel):
    """A single adoration time slot."""

    day: DayOfWeek
    start_time: int = Field(..., ge=0, le=2359, description="24hr format")
    end_time: int = Field(..., ge=0, le=2359, description="24hr format")
    notes: Optional[str] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_format(cls, v: int) -> int:
        minutes = v % 100
        if minutes > 59:
            raise ValueError(f"Invalid time {v}: minutes must be 0-59")
        return v


class AdorationSchedule(BaseModel):
    """Eucharistic adoration schedule."""

    is_perpetual: bool = Field(False, description="True if 24/7 perpetual adoration")
    times: list[AdorationTime] = Field(default_factory=list)


class ParishContact(BaseModel):
    """Parish contact and location information."""

    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipcode: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None


class ParishEvent(BaseModel):
    """A parish event (retreat, fish fry, bible study, etc.)."""

    name: str
    description: Optional[str] = None
    frequency: EventFrequency

    # For one-time events
    event_date: Optional[date] = None

    # For recurring events
    day_of_week: Optional[DayOfWeek] = None
    time: Optional[int] = Field(None, ge=0, le=2359)

    location: Optional[str] = Field(
        None, description="If different from main church"
    )
    contact: Optional[str] = None
    cost: Optional[str] = None

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            minutes = v % 100
            if minutes > 59:
                raise ValueError(f"Invalid time {v}: minutes must be 0-59")
        return v


class BulletinExtraction(BaseModel):
    """
    Complete extraction from a parish bulletin.
    This is the single schema sent to GPT-4o for structured output.
    """

    parish_info: ParishContact = Field(default_factory=ParishContact)

    mass_times: list[MassTime] = Field(
        default_factory=list, description="Regular weekly Mass schedule"
    )

    confession_times: list[ConfessionTime] = Field(
        default_factory=list, description="Regular confession schedule"
    )

    adoration: AdorationSchedule = Field(
        default_factory=AdorationSchedule, description="Eucharistic adoration schedule"
    )

    events: list[ParishEvent] = Field(
        default_factory=list,
        description="Parish events: retreats, fish fries, bible studies, etc.",
    )

    events_summary: Optional[str] = Field(
        None,
        description="A brief 2-3 sentence summary of upcoming events and activities at this parish",
    )

    extraction_notes: Optional[str] = Field(
        None, description="Any issues or ambiguities encountered during extraction"
    )


# Database record types (for type hints in database abstraction)


class ParishRecord(BaseModel):
    """Minimal parish record from database."""

    parish_id: str
    name: str
    enabled: bool
    publisher: str  # "Parishes Online", "Discover Mass", "eCatholic"
    last_run: Optional[date] = None
