import json
from pathlib import Path

import pytest

from bank_parser import statement_from_dict

FIXTURES = Path(__file__).parent / "fixtures"
SBI_EXPECTED = FIXTURES / "sbi_sample.expected.json"


@pytest.fixture
def sbi_raw():
    """SBI ground truth in serialized form (a fresh copy per test).

    Expected outputs contain statement data and are gitignored, so tests that
    need one are skipped on a fresh clone (see tests/fixtures/README.md).
    """
    if not SBI_EXPECTED.exists():
        pytest.skip(f"local-only fixture missing: {SBI_EXPECTED.name}")
    with open(SBI_EXPECTED, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def sbi_statement(sbi_raw):
    return statement_from_dict(sbi_raw)
