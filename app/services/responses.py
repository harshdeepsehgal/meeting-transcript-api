"""Generate and persist responses for complete dialogs."""

import logging
from dataclasses import dataclass

import sqlalchemy as sa
from openai import APIStatusError, OpenAIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DialogTurn
from app.integrations.openai import (
    OpenAIProvider,
    is_context_limit_error,
    request_dialog_responses,
)
from app.services.dialogs import get_dialog_detail
from app.services.ingestion import render_transcript

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DialogResponseResult:
    """One stored answer paired with the current generation attempt."""

    query: str
    stored_response: str
    generated_response: str | None
    error_code: str | None = None
    error_message: str | None = None


async def generate_dialog_responses(
    session: AsyncSession,
    *,
    dialog_id: str,
    provider: OpenAIProvider | None,
) -> list[DialogResponseResult] | None:
    """Generate every turn response in one provider request and persist it atomically."""
    async with session.begin():
        detail = await get_dialog_detail(session, dialog_id=dialog_id)
    if detail is None:
        return None

    _, segments, turns = detail
    if not turns:
        return []
    if provider is None:
        return _error_results(
            turns,
            code="openai_not_configured",
            message="OpenAI API key is not configured",
        )

    try:
        generated = await request_dialog_responses(
            provider,
            render_transcript(segments),
            [(turn.position, turn.query) for turn in turns],
        )
    except APIStatusError as exc:
        if is_context_limit_error(exc):
            logger.warning(
                "Dialog responses rejected by provider context limit: dialog_id=%r",
                dialog_id,
            )
            return _error_results(
                turns,
                code="context_limit_exceeded",
                message="Transcript and queries exceed model context limit",
            )
        logger.error(
            "Dialog response provider status failure: dialog_id=%r status_code=%s",
            dialog_id,
            exc.status_code,
        )
        return _error_results(
            turns,
            code="provider_failed",
            message="Response provider failed",
        )
    except (OpenAIError, ValueError) as exc:
        logger.error(
            "Dialog response generation failure: dialog_id=%r error_type=%s",
            dialog_id,
            type(exc).__name__,
        )
        return _error_results(
            turns,
            code="provider_failed",
            message="Response provider failed",
        )

    generated_by_position = {item.position: item.response for item in generated}
    async with session.begin():
        for position, generated_response in generated_by_position.items():
            await session.execute(
                sa.update(DialogTurn)
                .where(
                    DialogTurn.dialog_id == dialog_id,
                    DialogTurn.position == position,
                )
                .values(generated_response=generated_response)
            )

    logger.info(
        "Persisted generated dialog responses: dialog_id=%r responses=%d",
        dialog_id,
        len(generated),
    )
    return [
        DialogResponseResult(
            query=turn.query,
            stored_response=turn.response,
            generated_response=generated_by_position[turn.position],
        )
        for turn in turns
    ]


def _error_results(
    turns: list[DialogTurn],
    *,
    code: str,
    message: str,
) -> list[DialogResponseResult]:
    return [
        DialogResponseResult(
            query=turn.query,
            stored_response=turn.response,
            generated_response=None,
            error_code=code,
            error_message=message,
        )
        for turn in turns
    ]


__all__ = ["DialogResponseResult", "generate_dialog_responses"]
