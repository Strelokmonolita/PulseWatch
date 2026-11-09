"""Persistence and atomic rotation of single-use refresh tokens."""

from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RefreshToken


class RefreshTokenRepository:
    """Keep refresh-token state shared between API processes and restarts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, token: RefreshToken) -> None:
        try:
            await self._delete_expired(token.user_id)
            self.session.add(token)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def rotate(self, token_hash: str, replacement: RefreshToken) -> bool:
        """Consume the old token and save its replacement in one transaction."""
        try:
            consumed = await self.session.scalar(
                delete(RefreshToken)
                .where(
                    RefreshToken.token_hash == token_hash,
                    RefreshToken.user_id == replacement.user_id,
                    RefreshToken.expires_at > datetime.now(UTC),
                )
                .returning(RefreshToken.token_hash)
            )
            if consumed is None:
                await self.session.rollback()
                return False

            await self._delete_expired(replacement.user_id)
            self.session.add(replacement)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return True

    async def _delete_expired(self, user_id: int) -> None:
        await self.session.execute(
            delete(RefreshToken).where(
                RefreshToken.user_id == user_id,
                RefreshToken.expires_at <= datetime.now(UTC),
            )
        )
