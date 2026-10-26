from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from market_analysis.repository import _json_dumps


def test_json_dumps_serializes_repository_scalar_types() -> None:
    payload = {
        "candidate_id": UUID("443d5bcf-bb31-4abd-b015-842168a13fb3"),
        "observed_at": datetime(2026, 10, 26, 15, 20, tzinfo=UTC),
        "score": Decimal("42.50"),
    }

    serialized = _json_dumps(payload)

    assert '"candidate_id": "443d5bcf-bb31-4abd-b015-842168a13fb3"' in serialized
    assert '"observed_at": "2026-10-26T15:20:00+00:00"' in serialized
    assert '"score": "42.50"' in serialized


def test_json_dumps_still_rejects_unknown_objects() -> None:
    with pytest.raises(TypeError):
        _json_dumps({"value": object()})
