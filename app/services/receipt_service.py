"""ReceiptService — NGO acknowledgment PDFs (PRD 05).

Two documents, both rendered from HTML via weasyprint **off the event loop**
(``run_in_executor`` — PDF rendering is CPU-bound and would otherwise block all
concurrent requests):

  • a per-donation acknowledgment (the donor's proof, with the gapless receipt
    number, transaction id, gross amount, masjid, category, date), and
  • an annual giving summary for a year of donations.

These are payment acknowledgments, not tax instruments: the tax-deductibility
wording renders only when the platform flag ``tax_deductible_receipts_enabled``
is on (the NGO flips it after confirming NBR approval). weasyprint is imported
lazily so the app boots even where its native libraries are absent; the endpoint
returns 503 in that case.
"""

import asyncio
import html
import logging
import uuid
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import CurrentUser
from app.core.time import DHAKA_TZ
from app.models.donation import Donation
from app.models.enums import DonationStatus
from app.repositories.donation_repository import DonationRepository
from app.repositories.masjid_repository import MasjidRepository
from app.repositories.platform_settings_repository import PlatformSettingsRepository

logger = logging.getLogger(__name__)

# weasyprint's font subsetter logs every glyph at INFO — far too chatty for a
# request path. Keep its and weasyprint's own logs at WARNING.
logging.getLogger("fontTools").setLevel(logging.WARNING)
logging.getLogger("weasyprint").setLevel(logging.WARNING)

_TAX_LINE = (
    "This donation is tax-deductible under the donee organisation's approved "
    "status. Retain this receipt for your records."
)


def _render_pdf(doc_html: str) -> bytes:
    """Render HTML → PDF bytes. Runs in a worker thread (CPU-bound). weasyprint
    is imported here so a missing native lib degrades to a 503, not an
    import-time crash of the whole app."""
    try:
        from weasyprint import HTML

        return HTML(string=doc_html).write_pdf()
    except Exception as exc:  # missing native lib (import) OR a render-time error
        raise RuntimeError(f"PDF render failed: {exc}") from exc


class ReceiptService:
    def __init__(self, db: AsyncSession) -> None:
        self.repo = DonationRepository(db)
        self.masjid_repo = MasjidRepository(db)
        self.settings_repo = PlatformSettingsRepository(db)

    async def _tax_enabled(self) -> bool:
        platform = await self.settings_repo.get_or_create()
        return bool(platform.tax_deductible_receipts_enabled)

    async def _masjid_name(self, masjid_id: uuid.UUID) -> str:
        masjid = await self.masjid_repo.get_by_id(masjid_id)
        return masjid.name if masjid else "(masjid)"

    async def donation_receipt(
        self, donation_id: uuid.UUID, user: CurrentUser
    ) -> bytes:
        donation = await self.repo.get_by_id(donation_id)
        if donation is None or donation.user_id != user.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found"
            )
        if donation.status != DonationStatus.COMPLETED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A receipt is available only for a completed donation",
            )
        masjid_name = await self._masjid_name(donation.masjid_id)
        doc_html = self._receipt_html(
            donation, masjid_name, tax_enabled=await self._tax_enabled()
        )
        return await self._to_pdf(doc_html)

    async def annual_summary(self, user: CurrentUser, year: int) -> bytes:
        donations = await self.repo.list_completed_for_user_year(user.user_id, year)
        names: dict[uuid.UUID, str] = {}
        for d in donations:
            if d.masjid_id not in names:
                names[d.masjid_id] = await self._masjid_name(d.masjid_id)
        doc_html = self._summary_html(
            user, year, donations, names, tax_enabled=await self._tax_enabled()
        )
        return await self._to_pdf(doc_html)

    async def _to_pdf(self, doc_html: str) -> bytes:
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None, _render_pdf, doc_html
            )
        except RuntimeError as exc:
            logger.error("Receipt render failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Receipt generation is temporarily unavailable",
            ) from exc

    # ── Templates ─────────────────────────────────────────────────────────────

    def _header(self) -> str:
        ngo = html.escape(settings.NGO_NAME)
        reg = ""
        if settings.NGO_REGISTRATION_NUMBER:
            reg_no = html.escape(settings.NGO_REGISTRATION_NUMBER)
            reg = f"<div class='reg'>Reg. No. {reg_no}</div>"
        return f"<div class='hdr'><div class='ngo'>{ngo}</div>{reg}</div>"

    def _receipt_html(self, d: Donation, masjid_name: str, *, tax_enabled: bool) -> str:
        when = (
            d.completed_at.astimezone(DHAKA_TZ).strftime("%d %b %Y")
            if d.completed_at
            else "—"
        )
        tax = f"<p class='tax'>{_TAX_LINE}</p>" if tax_enabled else ""
        return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
{self._header()}
<h1>Donation Acknowledgment</h1>
<table>
  <tr><td>Receipt No.</td><td>{html.escape(d.receipt_number or "—")}</td></tr>
  <tr><td>Transaction ID</td><td>{d.donation_id}</td></tr>
  <tr><td>Date</td><td>{when}</td></tr>
  <tr><td>Donor</td><td>{html.escape(d.donor_name or "—")}</td></tr>
  <tr><td>Masjid</td><td>{html.escape(masjid_name)}</td></tr>
  <tr><td>Category</td><td>{html.escape(d.category.title())}</td></tr>
  <tr><td>Amount</td><td>৳{d.gross_amount:,.2f}</td></tr>
</table>
<p class="thanks">Received with thanks. JazakAllah khairan.</p>
{tax}
<p class="foot">This is a payment acknowledgment
issued by {html.escape(settings.NGO_NAME)}.</p>
</body></html>"""

    def _summary_html(
        self,
        user: CurrentUser,
        year: int,
        donations: list[Donation],
        names: dict[uuid.UUID, str],
        *,
        tax_enabled: bool,
    ) -> str:
        total = sum((d.gross_amount for d in donations), Decimal("0.00"))

        def _row(d: Donation) -> str:
            day = (
                d.completed_at.astimezone(DHAKA_TZ).strftime("%d %b")
                if d.completed_at
                else "—"
            )
            masjid = html.escape(names.get(d.masjid_id, "—"))
            return (
                f"<tr><td>{day}</td><td>{masjid}</td>"
                f"<td>{html.escape(d.category.title())}</td>"
                f"<td class='r'>৳{d.gross_amount:,.2f}</td></tr>"
            )

        rows = "".join(_row(d) for d in donations)
        tax = f"<p class='tax'>{_TAX_LINE}</p>" if tax_enabled else ""
        empty = (
            "<p>No completed donations recorded for this year.</p>"
            if not donations
            else ""
        )
        return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body>
{self._header()}
<h1>Annual Giving Summary — {year}</h1>
<p>Donor: {html.escape(user.email or str(user.user_id))}</p>
{empty}
<table class="list">
  <thead><tr><th>Date</th><th>Masjid</th>
  <th>Category</th><th class="r">Amount</th></tr></thead>
  <tbody>{rows}</tbody>
  <tfoot><tr><td colspan="3">Total</td><td class="r">৳{total:,.2f}</td></tr></tfoot>
</table>
{tax}
<p class="foot">Issued by {html.escape(settings.NGO_NAME)}
as a record of your giving.</p>
</body></html>"""


_CSS = """
  body { font-family: sans-serif; color: #1a1a1a; margin: 40px; }
  .hdr { border-bottom: 2px solid #0a7d4b; padding-bottom: 8px; margin-bottom: 24px; }
  .ngo { font-size: 20px; font-weight: 700; color: #0a7d4b; }
  .reg { font-size: 11px; color: #555; }
  h1 { font-size: 18px; margin: 16px 0; }
  table { border-collapse: collapse; width: 100%; margin: 12px 0; }
  td, th { padding: 6px 8px; border-bottom: 1px solid #e3e3e3;
    font-size: 13px; text-align: left; }
  td:first-child { color: #555; width: 32%; }
  table.list td:first-child { width: auto; color: #1a1a1a; }
  .r { text-align: right; }
  tfoot td { font-weight: 700; border-top: 2px solid #1a1a1a; }
  .thanks { margin-top: 20px; font-weight: 600; }
  .tax { margin-top: 16px; padding: 10px; background: #eef7f1; font-size: 12px; }
  .foot { margin-top: 28px; font-size: 11px; color: #777; }
"""
