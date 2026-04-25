import logging

import aioboto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """
    Async S3/MinIO client. Instantiated per-request (no singleton).
    Session is created inside upload() and cleaned up on exit.
    """

    async def upload(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str,
    ) -> str:
        """Upload bytes to S3/MinIO. Returns the key. Raises 503 on backend failure."""
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                region_name=settings.S3_REGION,
                aws_access_key_id=settings.aws_key,
                aws_secret_access_key=settings.aws_secret,
                config=Config(signature_version="v4"),
            ) as s3:
                await s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "S3 upload failed",
                extra={"bucket": bucket, "key": key, "error": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage service unavailable — upload failed",
            )
        return key

    async def delete(self, bucket: str, key: str) -> None:
        """Delete an object from S3/MinIO. Raises 503 on backend failure."""
        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                endpoint_url=settings.s3_endpoint,
                region_name=settings.S3_REGION,
                aws_access_key_id=settings.aws_key,
                aws_secret_access_key=settings.aws_secret,
                config=Config(signature_version="v4"),
            ) as s3:
                await s3.delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "S3 delete failed",
                extra={"bucket": bucket, "key": key, "error": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Storage service unavailable — delete failed",
            )
