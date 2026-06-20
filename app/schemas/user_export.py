"""Schemas for the full user data export (PRD 09 §data portability).

`GET /users/me/export` returns the user's complete data, not just profile +
follows. Each item schema mirrors the meaningful columns of one user-linked
model and is populated via `model_validate` (`from_attributes`) directly from
the ORM rows in `UserService.export_me`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import FavouriteMasjidResponse


class _ExportItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DonationExport(_ExportItem):
    donation_id: uuid.UUID
    masjid_id: uuid.UUID
    campaign_id: uuid.UUID | None
    category: str
    status: str
    gross_amount: Decimal
    fee_amount: Decimal
    net_amount: Decimal
    is_anonymous: bool
    receipt_number: str | None
    completed_at: datetime | None
    refunded_at: datetime | None
    created_at: datetime


class ReviewExport(_ExportItem):
    review_id: uuid.UUID
    masjid_id: uuid.UUID
    rating: int
    body: str | None
    reviewer_display_name: str | None
    edited: bool
    created_at: datetime
    updated_at: datetime


class QuestionExport(_ExportItem):
    question_id: uuid.UUID
    masjid_id: uuid.UUID
    question: str
    status: str
    answer: str | None
    answer_author_role: str | None
    answered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubmissionExport(_ExportItem):
    submission_id: uuid.UUID
    name: str
    latitude: float
    longitude: float
    address: str | None
    photo_key: str | None
    status: str
    approved_masjid_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CheckinExport(_ExportItem):
    checkin_id: uuid.UUID
    masjid_id: uuid.UUID | None
    checked_in_at: datetime


class JournalEntryExport(_ExportItem):
    journal_id: uuid.UUID
    entry_date: date
    fajr: bool
    dhuhr: bool
    asr: bool
    maghrib: bool
    isha: bool
    quran_amount: int | None
    quran_unit: str | None
    is_protected: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class GoalExport(_ExportItem):
    goal_id: uuid.UUID
    goal_kind: str
    template: str | None
    title: str
    status: str
    target_amount: int | None
    unit: str | None
    start_date: date | None
    end_date: date | None
    recurrence: str | None
    # Populated in the service from `completion_dates(goal_id)` — the ORM
    # `completions` relationship is lazy="raise", so it is never loaded here.
    completion_dates: list[date] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class BadgeExport(_ExportItem):
    badge_id: uuid.UUID
    badge_type: str
    tier: int
    earned_at: datetime


class SupportTicketExport(_ExportItem):
    ticket_id: uuid.UUID
    category: str
    subject: str | None
    description: str | None
    donation_id: uuid.UUID | None
    status: str
    created_at: datetime
    updated_at: datetime


class DeviceTokenExport(_ExportItem):
    device_token_id: uuid.UUID
    token: str
    platform: str
    created_at: datetime
    last_seen_at: datetime


class ReportExport(_ExportItem):
    report_id: uuid.UUID
    masjid_id: uuid.UUID | None
    field_name: str
    description: str
    reporter_email: str | None
    status: str
    created_at: datetime


class RecurringScheduleExport(_ExportItem):
    schedule_id: uuid.UUID
    masjid_id: uuid.UUID
    campaign_id: uuid.UUID | None
    category: str
    amount: Decimal
    frequency: str
    start_date: date
    end_date: date | None
    next_due_at: datetime
    status: str
    created_at: datetime
    updated_at: datetime


class EventRsvpExport(_ExportItem):
    event_id: uuid.UUID
    rsvp_at: datetime


class UserDataExport(BaseModel):
    """The full export payload — profile + every user-linked collection."""

    exported_at: datetime
    user_id: uuid.UUID
    email: str | None
    display_name: str | None
    madhab: str | None
    profile_photo_url: str | None
    created_at: datetime
    followed_masjids: list[FavouriteMasjidResponse]
    donations: list[DonationExport]
    reviews: list[ReviewExport]
    questions: list[QuestionExport]
    submissions: list[SubmissionExport]
    checkins: list[CheckinExport]
    journal_entries: list[JournalEntryExport]
    goals: list[GoalExport]
    badges: list[BadgeExport]
    support_tickets: list[SupportTicketExport]
    device_tokens: list[DeviceTokenExport]
    reports: list[ReportExport]
    recurring_schedules: list[RecurringScheduleExport]
    event_rsvps: list[EventRsvpExport]
