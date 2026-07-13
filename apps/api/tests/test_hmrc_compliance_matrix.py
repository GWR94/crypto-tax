"""End-to-end HMRC compliance matrix (normalize → CGT / income / data health).

Run from repo root::

    npm run test:hmrc-matrix

``known_gap`` rows document deliberate policy gaps and assert stable behaviour.
"""

from __future__ import annotations

import pytest

from app.hmrc_matrix import (
    HMRC_MATRIX_CASES,
    MatrixStatus,
    matrix_summary,
    run_matrix_case,
)

_PASS_CASES = [c.case_id for c in HMRC_MATRIX_CASES if c.status == MatrixStatus.PASS]
_KNOWN_GAP_CASES = [
    c.case_id for c in HMRC_MATRIX_CASES if c.status == MatrixStatus.KNOWN_GAP
]


@pytest.mark.parametrize("case_id", _PASS_CASES)
def test_hmrc_matrix_pass(case_id: str) -> None:
    run_matrix_case(case_id)


@pytest.mark.parametrize("case_id", _KNOWN_GAP_CASES)
def test_hmrc_matrix_known_gap(case_id: str) -> None:
    """Documents HMRC mismatch; fails if engine behaviour changes unintentionally."""
    run_matrix_case(case_id)


def test_matrix_catalogue_complete() -> None:
    """Every registered case has a fixture builder and assertion handler."""
    from app.hmrc_matrix import FIXTURE_BUILDERS

    assert len(HMRC_MATRIX_CASES) == len(FIXTURE_BUILDERS)
    for case in HMRC_MATRIX_CASES:
        assert case.case_id in FIXTURE_BUILDERS
        fixture = FIXTURE_BUILDERS[case.case_id]()
        assert fixture, f"{case.case_id} fixture is empty"


def test_matrix_summary_export() -> None:
    summary = matrix_summary()
    assert len(summary) == len(HMRC_MATRIX_CASES)
    statuses = {row["status"] for row in summary}
    assert statuses == {"pass", "known_gap"}
