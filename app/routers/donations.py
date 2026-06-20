"""Donation HTTP layer (PRD 05) — user dashboard + admin views.

Dual-router (the support.py pattern): ``user_router`` is JWT-gated donor
surfaces; ``admin_router`` is role-gated masjid/platform views. HTTP only — every
decision lives in DonationService. The IPN webhook and gateway redirects live in
``payments.py`` (unauthenticated). Recurring-schedule CRUD is added in Step 6.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.security import CurrentUser
from app.dependencies.auth import (
    get_current_user,
    require_masjid_admin,
    require_platform_admin,
)
from app.dependencies.donation import (
    get_donation_service,
    get_receipt_service,
    get_recurring_schedule_service,
)
from app.schemas.disbursement import (
    BalanceListResponse,
    BalanceResponse,
    DisbursementCreate,
    DisbursementResponse,
    RefundRequest,
)
from app.schemas.donation import (
    AdminDonationListResponse,
    CampaignDonationCreate,
    CheckoutInitResponse,
    DonationCreate,
    DonationHistoryResponse,
    DonationStatusResponse,
    DonationSummaryResponse,
)
from app.schemas.recurring_schedule import (
    RecurringScheduleCreate,
    RecurringScheduleListResponse,
    RecurringScheduleResponse,
    RecurringScheduleUpdate,
)
from app.services.donation_service import DonationService
from app.services.receipt_service import ReceiptService
from app.services.recurring_schedule_service import RecurringScheduleService

user_router = APIRouter(tags=["donations"])
admin_router = APIRouter(prefix="/admin", tags=["donations"])


def _checkout_response(result, service: DonationService) -> CheckoutInitResponse:
    d = result.donation
    return CheckoutInitResponse(
        donation_id=d.donation_id,
        status=d.status,
        gross_amount=d.gross_amount,
        estimated_net=service.estimate_net(d.gross_amount),
        gateway_url=result.gateway_url,
    )


# ── Checkout init ────────────────────────────────────────────────────────────


@user_router.post(
    "/masjids/{masjid_id}/donations",
    response_model=CheckoutInitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a donation to a masjid → PENDING row + gateway URL",
)
async def create_masjid_donation(
    masjid_id: uuid.UUID,
    body: DonationCreate,
    user: CurrentUser = Depends(get_current_user),
    service: DonationService = Depends(get_donation_service),
) -> CheckoutInitResponse:
    result = await service.create_pending(
        user,
        masjid_id=masjid_id,
        amount=body.amount,
        category=body.category.value,
        is_anonymous=body.is_anonymous,
        donor_name=body.donor_name,
    )
    return _checkout_response(result, service)


@user_router.post(
    "/campaigns/{campaign_id}/donations",
    response_model=CheckoutInitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a campaign donation → masjid derived, category forced CAMPAIGN",
)
async def create_campaign_donation(
    campaign_id: uuid.UUID,
    body: CampaignDonationCreate,
    user: CurrentUser = Depends(get_current_user),
    service: DonationService = Depends(get_donation_service),
) -> CheckoutInitResponse:
    result = await service.create_pending(
        user,
        masjid_id=None,  # derived from the campaign
        amount=body.amount,
        category="campaign",
        is_anonymous=body.is_anonymous,
        donor_name=body.donor_name,
        campaign_id=campaign_id,
    )
    return _checkout_response(result, service)


# ── Status poll + dashboard ──────────────────────────────────────────────────


@user_router.get(
    "/donations/{donation_id}",
    response_model=DonationStatusResponse,
    summary="Poll a donation's status (owner-only)",
)
async def get_donation_status(
    donation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: DonationService = Depends(get_donation_service),
) -> DonationStatusResponse:
    return await service.get_owned_status(donation_id, user)


@user_router.get(
    "/me/donations",
    response_model=DonationHistoryResponse,
    summary="My donation history, newest first, filterable & cursor-paginated",
)
async def list_my_donations(
    masjid_id: uuid.UUID | None = Query(default=None),
    category: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    year: int | None = Query(default=None),
    cursor: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(get_current_user),
    service: DonationService = Depends(get_donation_service),
) -> DonationHistoryResponse:
    return await service.get_history(
        user,
        masjid_id=masjid_id,
        category=category,
        status_=status_filter,
        year=year,
        cursor=cursor,
        limit=limit,
    )


@user_router.get(
    "/me/donations/summary",
    response_model=DonationSummaryResponse,
    summary="Lifetime / this-year / per-masjid giving totals (gross)",
)
async def my_donation_summary(
    user: CurrentUser = Depends(get_current_user),
    service: DonationService = Depends(get_donation_service),
) -> DonationSummaryResponse:
    return await service.get_summary(user)


# ── Receipts (PDF, weasyprint, executor-offloaded) ────────────────────────────


@user_router.get(
    "/donations/{donation_id}/receipt",
    summary="Download a per-donation acknowledgment PDF (completed, owner-only)",
    response_class=Response,
)
async def download_receipt(
    donation_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: ReceiptService = Depends(get_receipt_service),
) -> Response:
    pdf = await service.donation_receipt(donation_id, user)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="receipt-{donation_id}.pdf"'
        },
    )


@user_router.get(
    "/me/donations/annual-report",
    summary="Download an annual giving-summary PDF for a year",
    response_class=Response,
)
async def download_annual_report(
    year: int = Query(..., ge=2020, le=2100),
    user: CurrentUser = Depends(get_current_user),
    service: ReceiptService = Depends(get_receipt_service),
) -> Response:
    pdf = await service.annual_summary(user, year)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="giving-summary-{year}.pdf"'
        },
    )


# ── Recurring schedules (reminder engine, never auto-charge) ──────────────────


@user_router.post(
    "/me/recurring-schedules",
    response_model=RecurringScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recurring donation reminder (incl. last-10-nights preset)",
)
async def create_recurring_schedule(
    body: RecurringScheduleCreate,
    user: CurrentUser = Depends(get_current_user),
    service: RecurringScheduleService = Depends(get_recurring_schedule_service),
) -> RecurringScheduleResponse:
    return await service.create(user, body)


@user_router.get(
    "/me/recurring-schedules",
    response_model=RecurringScheduleListResponse,
    summary="List my recurring schedules (masjid, amount, frequency, next due)",
)
async def list_recurring_schedules(
    user: CurrentUser = Depends(get_current_user),
    service: RecurringScheduleService = Depends(get_recurring_schedule_service),
) -> RecurringScheduleListResponse:
    return await service.list_for_user(user)


@user_router.patch(
    "/me/recurring-schedules/{schedule_id}",
    response_model=RecurringScheduleResponse,
    summary="Pause / resume / change amount of a recurring schedule",
)
async def update_recurring_schedule(
    schedule_id: uuid.UUID,
    body: RecurringScheduleUpdate,
    user: CurrentUser = Depends(get_current_user),
    service: RecurringScheduleService = Depends(get_recurring_schedule_service),
) -> RecurringScheduleResponse:
    return await service.update(schedule_id, user, body)


@user_router.delete(
    "/me/recurring-schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel a recurring schedule",
)
async def cancel_recurring_schedule(
    schedule_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    service: RecurringScheduleService = Depends(get_recurring_schedule_service),
) -> None:
    await service.cancel(schedule_id, user)


# ── Admin: masjid-scoped donations + balances + disbursements + refund ────────


@admin_router.get(
    "/masjids/{masjid_id}/donations",
    response_model=AdminDonationListResponse,
    summary="Masjid-scoped donations (masjid_admin: own; platform_admin: any)",
)
async def admin_list_masjid_donations(
    masjid_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_masjid_admin),
    service: DonationService = Depends(get_donation_service),
) -> AdminDonationListResponse:
    return await service.list_masjid_donations(
        user, masjid_id, status_=status_filter, page=page, page_size=page_size
    )


@admin_router.get(
    "/masjids/{masjid_id}/balance",
    response_model=BalanceResponse,
    summary="A masjid's derived balance (masjid_admin: own; platform_admin: any)",
)
async def admin_masjid_balance(
    masjid_id: uuid.UUID,
    user: CurrentUser = Depends(require_masjid_admin),
    service: DonationService = Depends(get_donation_service),
) -> BalanceResponse:
    return await service.get_masjid_balance(user, masjid_id)


@admin_router.get(
    "/balances",
    response_model=BalanceListResponse,
    summary="Per-masjid balances across the platform (platform_admin)",
)
async def admin_all_balances(
    _user: CurrentUser = Depends(require_platform_admin),
    service: DonationService = Depends(get_donation_service),
) -> BalanceListResponse:
    return await service.all_balances()


@admin_router.post(
    "/masjids/{masjid_id}/disbursements",
    response_model=DisbursementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a manual payout against a masjid's balance (platform_admin)",
)
async def admin_record_disbursement(
    masjid_id: uuid.UUID,
    body: DisbursementCreate,
    user: CurrentUser = Depends(require_platform_admin),
    service: DonationService = Depends(get_donation_service),
) -> DisbursementResponse:
    d = await service.record_disbursement(
        masjid_id=masjid_id,
        amount=body.amount,
        method=body.method.value,
        disbursed_on=body.disbursed_on,
        recorded_by_id=user.user_id,
        reference=body.reference,
        notes=body.notes,
    )
    return DisbursementResponse(
        disbursement_id=d.disbursement_id,
        masjid_id=d.masjid_id,
        amount=d.amount,
        method=d.method,
        reference=d.reference,
        disbursed_on=d.disbursed_on,
        notes=d.notes,
        created_at=d.created_at,
    )


@admin_router.post(
    "/donations/{donation_id}/refund",
    response_model=DonationStatusResponse,
    summary="Refund a donation: gateway refund + campaign/balance reversal",
)
async def admin_refund_donation(
    donation_id: uuid.UUID,
    body: RefundRequest,
    _user: CurrentUser = Depends(require_platform_admin),
    service: DonationService = Depends(get_donation_service),
) -> DonationStatusResponse:
    return await service.refund_by_id(donation_id, body.reason)
