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


class SiteInfo(BaseModel):
    """A worship site/location within a parish (for multi-site parishes)."""

    site_name: str = Field(..., description="e.g., 'St. Mary Main Church', 'Holy Family Chapel'")
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipcode: Optional[str] = None

    mass_times: list[MassTime] = Field(
        default_factory=list, description="Mass schedule for this site"
    )
    confession_times: list[ConfessionTime] = Field(
        default_factory=list, description="Confession schedule for this site"
    )
    adoration: AdorationSchedule = Field(
        default_factory=AdorationSchedule,
        description="Adoration schedule for this site",
    )


class ParishContact(BaseModel):
    """Parish-level contact information (shared across all sites)."""

    name: Optional[str] = Field(None, description="Official name of the parish")
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

    Supports multi-site parishes: each site has its own address and schedules,
    while parish-level info (contact, events) is shared.
    """

    # Parish-level info (shared across all sites)
    parish_info: ParishContact = Field(default_factory=ParishContact)

    # Per-site data (one entry per worship location)
    sites: list[SiteInfo] = Field(
        default_factory=list,
        description="Worship sites/locations. Most parishes have one site. "
        "Multi-site parishes (or bulletins covering multiple parishes) have multiple.",
    )

    # Parish-wide events
    events: list[ParishEvent] = Field(
        default_factory=list,
        description="Parish events: retreats, fish fries, bible studies, etc.",
    )

    events_summary: Optional[str] = Field(
        None,
        description="A brief 2-3 sentence summary of upcoming events and activities",
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

    # Multi-site support: groups parishes that share a bulletin
    # If set, this parish shares a bulletin with the "primary" parish
    # The primary parish has bulletin_group_id == parish_id (or None)
    bulletin_group_id: Optional[str] = None

    @property
    def is_primary_site(self) -> bool:
        """True if this is the primary parish (owns the bulletin download)."""
        return self.bulletin_group_id is None or self.bulletin_group_id == self.parish_id

    @property
    def is_secondary_site(self) -> bool:
        """True if this parish gets data from another parish's bulletin."""
        return self.bulletin_group_id is not None and self.bulletin_group_id != self.parish_id
