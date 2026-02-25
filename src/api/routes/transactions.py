"""Transaction lookup and related-transaction endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import Transaction, get_db
from src.schemas.schemas import RelatedTransactionsResponse, TransactionResponse

logger = logging.getLogger(__name__)

transactions_router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@transactions_router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
) -> TransactionResponse:
    """Retrieve a single transaction by its ID.

    Args:
        transaction_id: Unique identifier of the transaction.
        db: Async database session dependency.

    Returns:
        Full transaction details.

    Raises:
        HTTPException: 404 if the transaction is not found.
    """
    stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction '{transaction_id}' not found",
        )

    return TransactionResponse.model_validate(txn)


@transactions_router.get(
    "/{transaction_id}/related",
    response_model=RelatedTransactionsResponse,
)
async def get_related_transactions(
    transaction_id: str,
    db: AsyncSession = Depends(get_db),
) -> RelatedTransactionsResponse:
    """Find transactions related by email, IP, or card BIN.

    Searches for other transactions sharing the same customer_email,
    customer_ip, or card_bin as the target transaction.

    Args:
        transaction_id: Unique identifier of the anchor transaction.
        db: Async database session dependency.

    Returns:
        Related transactions grouped by matching dimension (email, IP, BIN).

    Raises:
        HTTPException: 404 if the anchor transaction is not found.
    """
    # Fetch the anchor transaction
    stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
    result = await db.execute(stmt)
    txn = result.scalar_one_or_none()

    if txn is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction '{transaction_id}' not found",
        )

    # Related by customer_email
    email_stmt = (
        select(Transaction)
        .where(Transaction.customer_email == txn.customer_email)
        .where(Transaction.transaction_id != transaction_id)
        .order_by(Transaction.timestamp.desc())
        .limit(20)
    )
    email_result = await db.execute(email_stmt)
    by_email = [
        TransactionResponse.model_validate(t) for t in email_result.scalars().all()
    ]

    # Related by customer_ip
    ip_stmt = (
        select(Transaction)
        .where(Transaction.customer_ip == txn.customer_ip)
        .where(Transaction.transaction_id != transaction_id)
        .order_by(Transaction.timestamp.desc())
        .limit(20)
    )
    ip_result = await db.execute(ip_stmt)
    by_ip = [
        TransactionResponse.model_validate(t) for t in ip_result.scalars().all()
    ]

    # Related by card_bin (skip if None)
    by_bin: list[TransactionResponse] = []
    if txn.card_bin is not None:
        bin_stmt = (
            select(Transaction)
            .where(Transaction.card_bin == txn.card_bin)
            .where(Transaction.transaction_id != transaction_id)
            .order_by(Transaction.timestamp.desc())
            .limit(20)
        )
        bin_result = await db.execute(bin_stmt)
        by_bin = [
            TransactionResponse.model_validate(t) for t in bin_result.scalars().all()
        ]

    return RelatedTransactionsResponse(
        transaction=TransactionResponse.model_validate(txn),
        related_by_email=by_email,
        related_by_ip=by_ip,
        related_by_bin=by_bin,
    )
