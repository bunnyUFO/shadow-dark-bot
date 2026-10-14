"""The guild's shared coffer: one singleton balance row, in copper pieces."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot.models import Coffer


def get_or_create_coffer(session: Session) -> Coffer:
    """Auto-create the singleton coffer row on first use."""
    coffer = session.scalars(select(Coffer).limit(1)).first()
    if coffer is None:
        coffer = Coffer(balance_cp=0)
        session.add(coffer)
        session.flush()
    return coffer
