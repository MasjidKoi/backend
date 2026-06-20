"""PRD 02 — the submission photo-upload endpoint.

Exercises the validation branches that run BEFORE the storage backend is
touched (so these pass without MinIO running). A real upload's success path
needs object storage and is covered by the manual verification in the plan.
"""

import uuid

import pytest

from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio

URL = "/masjids/submissions/photo"


async def test_upload_rejects_wrong_content_type(client):
    r = await client.post(
        URL,
        headers=auth_headers(uuid.uuid4()),
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 415


async def test_upload_rejects_empty_file(client):
    r = await client.post(
        URL,
        headers=auth_headers(uuid.uuid4()),
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert r.status_code == 422


async def test_upload_requires_auth(client):
    r = await client.post(
        URL,
        files={"file": ("x.png", b"\x89PNG\r\n", "image/png")},
    )
    assert r.status_code in (401, 403)
