from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.orm import Session

from db import SessionLocal


T = TypeVar("T")


def run_transactional(
    operation: Callable[[Session], T],
) -> T:
    """Run one database operation with a single transaction owner."""
    db = SessionLocal()
    try:
        result = operation(db)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
