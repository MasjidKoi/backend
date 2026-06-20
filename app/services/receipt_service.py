"""ReceiptService — NGO acknowledgment PDFs (PRD 05).

Two documents, both rendered from HTML via weasyprint **off the event loop**
(``run_in_executor`` — PDF rendering is CPU-bound and would otherwise block all
concurrent requests):

  • a per-donation acknowledgment styled as a Stripe-like receipt (donor's proof,
    with the gapless receipt number, a three-column amount/date/method summary,
    a line item, the gross→fee→net breakdown, and references), and
  • an annual giving statement for a year of donations (stat strip, line-item
    table, by-category subtotals).

These are payment acknowledgments, not tax instruments: the tax-deductibility
wording renders only when the platform flag ``tax_deductible_receipts_enabled``
is on (the NGO flips it after confirming NBR approval). weasyprint is imported
lazily so the app boots even where its native libraries are absent; the endpoint
returns 503 in that case.

The HTML builders (``_receipt_html`` / ``_summary_html`` / ``_header_html``) are
pure module-level functions taking already-fetched data, so they render with no
DB session — see ``scratchpad/preview_receipts.py`` for sample rendering.
"""

import asyncio
import html
import logging
import re
import uuid
from datetime import datetime
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


# ── Formatting helpers (pure) ──────────────────────────────────────────────────


def _money(amount: Decimal) -> str:
    """Currency string in BDT — the single source for money formatting."""
    return f"৳{amount:,.2f}"


def _esc(value: str | None, fallback: str = "—") -> str:
    return html.escape(value) if value else fallback


def _fmt_date(dt: datetime | None) -> str:
    return dt.astimezone(DHAKA_TZ).strftime("%d %b %Y") if dt else "—"


def _fmt_day(dt: datetime | None) -> str:
    return dt.astimezone(DHAKA_TZ).strftime("%d %b") if dt else "—"


def _payment_method(raw: str | None) -> str:
    """Humanize the gateway's payment-method string (e.g. ``"VISA-VISA"`` →
    "Visa", ``"DBBL_MOBILE"`` → "DBBL Mobile"); fall back to "Online"."""
    if not raw:
        return "Online"
    seen: list[str] = []
    for token in re.split(r"[\s_\-/]+", raw.strip()):
        if token and token.lower() not in {s.lower() for s in seen}:
            seen.append(token)
    words = [w.upper() if len(w) <= 4 else w.title() for w in seen]
    return " ".join(words) or "Online"


def _render_pdf(doc_html: str) -> bytes:
    """Render HTML → PDF bytes. Runs in a worker thread (CPU-bound). weasyprint
    is imported here so a missing native lib degrades to a 503, not an
    import-time crash of the whole app."""
    try:
        from weasyprint import HTML

        return HTML(string=doc_html).write_pdf()
    except Exception as exc:  # missing native lib (import) OR a render-time error
        raise RuntimeError(f"PDF render failed: {exc}") from exc


# ── HTML builders (pure — no DB, no request scope) ─────────────────────────────


def _issuer_lines() -> list[str]:
    """Issuer contact lines from settings — only those actually configured."""
    lines: list[str] = []
    if settings.NGO_REGISTRATION_NUMBER:
        lines.append(f"Reg. No. {html.escape(settings.NGO_REGISTRATION_NUMBER)}")
    if settings.NGO_ADDRESS:
        lines.append(html.escape(settings.NGO_ADDRESS))
    contact = []
    if settings.NGO_CONTACT_EMAIL:
        contact.append(html.escape(settings.NGO_CONTACT_EMAIL))
    if settings.NGO_WEBSITE:
        contact.append(html.escape(settings.NGO_WEBSITE))
    if contact:
        lines.append(" · ".join(contact))
    return lines


def _header_html(
    *, eyebrow: str, ref_label: str, ref_value: str, date_label: str
) -> str:
    """Stripe-style top bar: issuer block (left) + document meta (right)."""
    issuer = "".join(f"<div>{line}</div>" for line in _issuer_lines())
    return f"""<header class="topbar">
  <div class="issuer">
    <div class="brand">{_esc(settings.NGO_NAME)}</div>
    <div class="muted issuer-meta">{issuer}</div>
  </div>
  <div class="meta">
    <div class="eyebrow">{html.escape(eyebrow)}</div>
    <div class="meta-no">{html.escape(ref_value)}</div>
    <div class="muted">{html.escape(ref_label)}</div>
    <div class="muted">{html.escape(date_label)}</div>
  </div>
</header>"""


def _summary_band(cells: list[tuple[str, str, bool]]) -> str:
    """Three-column band (label / value); ``big`` flags the headline amount."""
    tds = []
    for i, (label, value, big) in enumerate(cells):
        cls = "cell" + (" first" if i == 0 else "")
        val_cls = "big" if big else "val"
        tds.append(
            f'<td class="{cls}"><div class="lbl">{html.escape(label)}</div>'
            f'<div class="{val_cls}">{value}</div></td>'
        )
    return f'<table class="band"><tr>{"".join(tds)}</tr></table>'


def _receipt_html(
    d: Donation,
    masjid_name: str,
    masjid_address: str,
    *,
    tax_enabled: bool,
) -> str:
    header = _header_html(
        eyebrow="Receipt",
        ref_label="Receipt number",
        ref_value=d.receipt_number or "—",
        date_label=f"Issued {_fmt_date(d.completed_at)}",
    )
    band = _summary_band(
        [
            ("Amount paid", _money(d.gross_amount), True),
            ("Date paid", _fmt_date(d.completed_at), False),
            ("Payment method", _esc(_payment_method(d.gateway_payment_method)), False),
        ]
    )
    desc_sub = " · ".join(
        p for p in (_esc(masjid_name), _esc(masjid_address, "")) if p and p != "—"
    )
    tax = f'<div class="tax">{_TAX_LINE}</div>' if tax_enabled else ""
    bank_ref = (
        f"<div>Bank Ref &nbsp;{_esc(d.gateway_bank_tran_id)}</div>"
        if d.gateway_bank_tran_id
        else ""
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body><div class="doc">
{header}
{band}
<table class="cols2"><tr>
  <td class="block">
    <div class="lbl">Billed to</div>
    <div class="strong">{_esc(d.donor_name)}</div>
    <div class="muted">{_esc(d.donor_email, "")}</div>
  </td>
  <td class="block right">
    <div class="lbl">Category</div>
    <div class="strong">{_esc(d.category.title())}</div>
  </td>
</tr></table>
<table class="items">
  <thead><tr><th>Description</th><th class="r">Qty</th>
  <th class="r">Amount</th></tr></thead>
  <tbody>
    <tr>
      <td><div class="strong">{_esc(d.category.title())} donation</div>
        <div class="muted">{desc_sub}</div></td>
      <td class="r">1</td>
      <td class="r">{_money(d.gross_amount)}</td>
    </tr>
  </tbody>
</table>
<table class="totals">
  <tr><td>Subtotal</td><td class="r">{_money(d.gross_amount)}</td></tr>
  <tr><td>Processing fee</td><td class="r">−{_money(d.fee_amount)}</td></tr>
  <tr><td>Net credited to masjid</td><td class="r">{_money(d.net_amount)}</td></tr>
  <tr class="grand"><td>Amount paid</td><td class="r">{_money(d.gross_amount)}</td></tr>
</table>
<div class="refs muted">
  <div>Transaction ID &nbsp;{d.donation_id}</div>
  {bank_ref}
</div>
<p class="thanks">Received with thanks. JazakAllah khairan.</p>
{tax}
<footer class="foot">This is a payment acknowledgment issued by
{_esc(settings.NGO_NAME)}. Keep it for your records.</footer>
</div></body></html>"""


def _summary_html(
    donor: str,
    year: int,
    donations: list[Donation],
    names: dict[uuid.UUID, str],
    *,
    tax_enabled: bool,
) -> str:
    total = sum((d.gross_amount for d in donations), Decimal("0.00"))
    masjids_supported = len({d.masjid_id for d in donations})

    header = _header_html(
        eyebrow="Statement",
        ref_label="Donor",
        ref_value=str(year),
        date_label=donor,
    )
    band = _summary_band(
        [
            ("Total given", _money(total), True),
            ("Donations", str(len(donations)), False),
            ("Masjids supported", str(masjids_supported), False),
        ]
    )

    def _row(d: Donation) -> str:
        return (
            f"<tr><td>{_fmt_day(d.completed_at)}</td>"
            f"<td class='mono'>{_esc(d.receipt_number)}</td>"
            f"<td>{_esc(names.get(d.masjid_id))}</td>"
            f"<td>{_esc(d.category.title())}</td>"
            f"<td class='r'>{_money(d.gross_amount)}</td></tr>"
        )

    rows = "".join(_row(d) for d in donations)
    empty = (
        '<p class="empty">No completed donations recorded for this year.</p>'
        if not donations
        else ""
    )

    by_cat: dict[str, Decimal] = {}
    for d in donations:
        by_cat[d.category] = by_cat.get(d.category, Decimal("0.00")) + d.gross_amount
    cat_rows = "".join(
        f"<tr><td>{_esc(cat.title())}</td><td class='r'>{_money(amt)}</td></tr>"
        for cat, amt in sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)
    )
    cat_box = (
        f'<div class="catbox"><div class="lbl">By category</div>'
        f'<table class="cat">{cat_rows}</table></div>'
        if donations
        else ""
    )
    tax = f'<div class="tax">{_TAX_LINE}</div>' if tax_enabled else ""

    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>{_CSS}</style></head><body><div class="doc">
{header}
<h1 class="title">Annual Giving Statement</h1>
<div class="subtitle muted">A record of your completed giving in {year}.</div>
{band}
{empty}
<table class="items list">
  <thead><tr><th>Date</th><th>Receipt No.</th><th>Masjid</th>
  <th>Category</th><th class="r">Amount</th></tr></thead>
  <tbody>{rows}</tbody>
  <tfoot><tr class="grand"><td colspan="4">Total given in {year}</td>
  <td class="r">{_money(total)}</td></tr></tfoot>
</table>
{cat_box}
{tax}
<footer class="foot">Issued by {_esc(settings.NGO_NAME)} as a record of your
giving. Individual receipts remain available from your donation history.</footer>
</div></body></html>"""


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
        masjid = await self.masjid_repo.get_by_id(donation.masjid_id)
        doc_html = _receipt_html(
            donation,
            masjid.name if masjid else "(masjid)",
            masjid.address if masjid else "",
            tax_enabled=await self._tax_enabled(),
        )
        return await self._to_pdf(doc_html)

    async def annual_summary(self, user: CurrentUser, year: int) -> bytes:
        donations = await self.repo.list_completed_for_user_year(user.user_id, year)
        names: dict[uuid.UUID, str] = {}
        for d in donations:
            if d.masjid_id not in names:
                names[d.masjid_id] = await self._masjid_name(d.masjid_id)
        doc_html = _summary_html(
            user.email or str(user.user_id),
            year,
            donations,
            names,
            tax_enabled=await self._tax_enabled(),
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


_CSS = """
  @page { size: A4; margin: 44px 52px; }
  * { box-sizing: border-box; }
  body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    color: #1a1f36; font-size: 13px; line-height: 1.5; margin: 0; }
  .doc { width: 100%; }
  .muted { color: #697386; }
  .strong { font-weight: 600; color: #1a1f36; }
  .lbl { font-size: 9.5px; letter-spacing: .07em; text-transform: uppercase;
    color: #8792a2; font-weight: 600; margin-bottom: 3px; }
  .r { text-align: right; }
  .mono { font-family: "SFMono-Regular", Menlo, Consolas, monospace; font-size: 11px; }

  /* Header */
  .topbar { display: table; width: 100%; padding-bottom: 22px;
    border-bottom: 3px solid #0a7d4b; margin-bottom: 28px; }
  .issuer { display: table-cell; vertical-align: top; }
  .meta { display: table-cell; vertical-align: top; text-align: right; }
  .brand { font-size: 19px; font-weight: 700; color: #0a7d4b; letter-spacing: -.01em; }
  .issuer-meta { font-size: 10.5px; margin-top: 5px; line-height: 1.45; }
  .eyebrow { font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
    color: #0a7d4b; font-weight: 700; }
  .meta-no { font-size: 16px; font-weight: 700; margin: 2px 0 4px; }

  /* Summary band */
  .band { width: 100%; border-collapse: collapse; background: #f7fafc;
    border: 1px solid #e3e8ee; border-radius: 8px; margin-bottom: 26px; }
  .band td.cell { padding: 16px 18px; vertical-align: top;
    border-left: 1px solid #e3e8ee; width: 33.33%; }
  .band td.first { border-left: none; }
  .band .big { font-size: 21px; font-weight: 700; color: #0a7d4b; }
  .band .val { font-size: 14px; font-weight: 600; }

  /* Billed-to / two columns */
  .cols2 { width: 100%; border-collapse: collapse; margin-bottom: 24px; }
  .cols2 .block { vertical-align: top; width: 50%; }
  .cols2 .right { text-align: right; }

  /* Line items */
  .items { width: 100%; border-collapse: collapse; margin-bottom: 8px; }
  .items thead th { font-size: 9.5px; letter-spacing: .07em; text-transform: uppercase;
    color: #8792a2; font-weight: 600; text-align: left;
    padding: 0 0 8px; border-bottom: 1px solid #e3e8ee; }
  .items tbody td { padding: 12px 0; border-bottom: 1px solid #eef1f6;
    vertical-align: top; }
  .items .muted { font-size: 11px; margin-top: 2px; }
  .items tfoot td { padding-top: 12px; }
  .list tbody td { padding: 9px 0; }

  /* Totals (right half) */
  .totals { width: 52%; margin-left: 48%; border-collapse: collapse; margin-top: 4px; }
  .totals td { padding: 6px 0; color: #4f566b; }
  .totals td.r { text-align: right; color: #1a1f36; }
  tr.grand td { border-top: 2px solid #1a1f36; padding-top: 11px;
    font-size: 15px; font-weight: 700; color: #1a1f36; }
  tr.grand td.r { color: #0a7d4b; }

  /* References, notes, footer */
  .refs { font-size: 10.5px; margin-top: 26px; line-height: 1.7; }
  .thanks { margin-top: 24px; font-weight: 600; font-size: 14px; }
  .tax { margin-top: 16px; padding: 12px 14px; background: #e8f3ee;
    border: 1px solid #cfe6da; border-radius: 6px; font-size: 11.5px; color: #1a4731; }
  .foot { margin-top: 30px; padding-top: 14px; border-top: 1px solid #eef1f6;
    font-size: 10.5px; color: #8792a2; }
  .empty { margin: 20px 0; color: #697386; }

  /* Annual statement extras */
  .title { font-size: 22px; font-weight: 700; margin: 4px 0 2px;
    letter-spacing: -.01em; }
  .subtitle { font-size: 12px; margin-bottom: 22px; }
  .catbox { margin-top: 26px; max-width: 320px; }
  .cat { width: 100%; border-collapse: collapse; margin-top: 8px; }
  .cat td { padding: 6px 0; border-bottom: 1px solid #eef1f6; font-size: 12px; }
  .cat td.r { color: #1a1f36; font-weight: 600; }
"""
