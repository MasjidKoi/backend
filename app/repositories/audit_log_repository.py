import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    model = AuditLog

    async def log(
        self,
        *,
        admin_id: uuid.UUID,
        admin_email: str | None,
        admin_role: str,
        action: str,
        target_entity: str | None = None,
        target_id: uuid.UUID | None = None,
        ip_address: str | None = None,
    ) -> None:
        """
        Append an audit record. Uses flush() NOT commit() — the calling
        service commits atomically after both the write and this log entry.
        """
        entry = AuditLog(
            admin_id=admin_id,
            admin_email=admin_email,
            admin_role=admin_role,
            action=action,
            target_entity=target_entity,
            target_id=target_id,
            ip_address=ip_address,
        )
        self.db.add(entry)
        await self.db.flush()

    async def get_paginated(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        count = (await self.db.execute(
            select(func.count()).select_from(AuditLog)
        )).scalar_one()

        rows = list((await self.db.execute(
            select(AuditLog)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )).scalars().all())

        return rows, count
