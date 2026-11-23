"""
Service layer for integration management
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import Integration
from typing import List, Optional, Dict
from datetime import datetime


class IntegrationService:
    """Business logic for integration operations"""

    @staticmethod
    async def create_integration(
        db: AsyncSession,
        user_id: str,
        vendor: str,
        name: str,
        credentials: Dict[str, str],
        url: Optional[str] = None,
        description: Optional[str] = None
    ) -> Integration:
        """Create a new integration"""
        integration = Integration(
            user_id=user_id,
            vendor=vendor,
            name=name,
            url=url,
            credentials=credentials,
            description=description,
            status="active"
        )
        db.add(integration)
        await db.commit()
        await db.refresh(integration)
        return integration

    @staticmethod
    async def get_integration(db: AsyncSession, integration_id: int) -> Optional[Integration]:
        """Get integration by ID"""
        result = await db.execute(select(Integration).where(Integration.id == integration_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_integrations(
        db: AsyncSession,
        user_id: str,
        vendor: Optional[str] = None
    ) -> List[Integration]:
        """Get all integrations for a user, optionally filtered by vendor"""
        query = select(Integration).where(Integration.user_id == user_id)
        if vendor:
            query = query.where(Integration.vendor == vendor)
        result = await db.execute(query.order_by(Integration.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def update_integration(
        db: AsyncSession,
        integration_id: int,
        name: Optional[str] = None,
        url: Optional[str] = None,
        credentials: Optional[Dict[str, str]] = None,
        description: Optional[str] = None,
        status: Optional[str] = None
    ) -> Optional[Integration]:
        """Update an existing integration"""
        result = await db.execute(select(Integration).where(Integration.id == integration_id))
        integration = result.scalar_one_or_none()
        if not integration:
            return None

        if name is not None:
            integration.name = name
        if url is not None:
            integration.url = url
        if credentials is not None:
            integration.credentials = credentials
        if description is not None:
            integration.description = description
        if status is not None:
            integration.status = status

        integration.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(integration)
        return integration

    @staticmethod
    async def delete_integration(db: AsyncSession, integration_id: int) -> bool:
        """Delete an integration"""
        result = await db.execute(select(Integration).where(Integration.id == integration_id))
        integration = result.scalar_one_or_none()
        if not integration:
            return False

        await db.delete(integration)
        await db.commit()
        return True

    @staticmethod
    async def disconnect_integration(db: AsyncSession, integration_id: int) -> Optional[Integration]:
        """Mark an integration as disconnected"""
        return await IntegrationService.update_integration(
            db,
            integration_id,
            status="disconnected"
        )

    @staticmethod
    async def update_last_sync(db: AsyncSession, integration_id: int) -> Optional[Integration]:
        """Update the last sync timestamp"""
        result = await db.execute(select(Integration).where(Integration.id == integration_id))
        integration = result.scalar_one_or_none()
        if not integration:
            return None

        integration.last_sync = datetime.utcnow()
        await db.commit()
        await db.refresh(integration)
        return integration

    @staticmethod
    def mask_credentials(credentials: Dict[str, str]) -> Dict[str, str]:
        """Mask sensitive credential fields for display"""
        masked = {}
        sensitive_fields = {"client_secret", "password", "secret_key", "access_token", "refresh_token"}

        for key, value in credentials.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                masked[key] = "••••••••" if value else ""
            else:
                masked[key] = value

        return masked
