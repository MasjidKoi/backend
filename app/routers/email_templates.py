"""
Internal email-template host.

GoTrue (v2.x) loads mailer templates from a URL, not a local file path. These
templates are served to GoTrue over the internal Compose network
(http://api:8000/email-templates/...) so the consumer email-OTP email can render
the 6-digit {{ .Token }} instead of GoTrue's default magic-link.

The bodies are static and contain only Go-template placeholders (no secrets), so
the route is unauthenticated — GoTrue fetches it server-to-server with no token.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/email-templates", tags=["internal"])

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "email_templates"
_ALLOWED = {"magic_link.html"}


@router.get(
    "/{name}",
    response_class=HTMLResponse,
    include_in_schema=False,
    summary="Serve a GoTrue email template (internal use)",
)
async def get_email_template(name: str) -> HTMLResponse:
    if name not in _ALLOWED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    path = _TEMPLATE_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))
