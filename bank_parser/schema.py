"""
Typed output contract for parsed bank statements.

Money is always Decimal. In serialized form (JSON) money is a plain decimal
string such as "120749.89" -- never a JSON number, so no value ever passes
through float.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
import re

SCHEMA_VERSION = "1"

_MONEY_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")


class SchemaError(ValueError):
    """Raised when serialized statement data does not match the contract."""


@dataclass(frozen=True)
class Transaction:
    row_index: int
    txn_date: date
    description: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None
    value_date: date | None = None
    reference: str | None = None
    page: int | None = None


@dataclass(frozen=True)
class StatementHeader:
    bank: str | None = None
    account_holder_name: str | None = None
    account_number: str | None = None
    ifsc: str | None = None
    branch: str | None = None
    currency: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


@dataclass(frozen=True)
class Statement:
    header: StatementHeader
    transactions: tuple[Transaction, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION


# --- parsing helpers -------------------------------------------------------

def parse_money(value, path):
    """Parse a serialized money value. None stays None; floats are rejected."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{path}: money must be a decimal string, got {type(value).__name__}")
    if not _MONEY_RE.match(value):
        raise SchemaError(f"{path}: invalid money value {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise SchemaError(f"{path}: invalid money value {value!r}") from None


def _parse_date(value, path, required=False):
    if value is None:
        if required:
            raise SchemaError(f"{path}: required date is missing")
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{path}: date must be an ISO string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SchemaError(f"{path}: invalid ISO date {value!r}") from None


def _parse_str(value, path, required=False):
    if value is None:
        if required:
            raise SchemaError(f"{path}: required string is missing")
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{path}: expected string, got {type(value).__name__}")
    return value


def _parse_int(value, path, required=False):
    if value is None:
        if required:
            raise SchemaError(f"{path}: required integer is missing")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{path}: expected integer, got {type(value).__name__}")
    return value


def transaction_from_dict(data, path="transaction"):
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: expected object, got {type(data).__name__}")
    return Transaction(
        row_index=_parse_int(data.get("row_index"), f"{path}.row_index", required=True),
        txn_date=_parse_date(data.get("txn_date"), f"{path}.txn_date", required=True),
        description=_parse_str(data.get("description"), f"{path}.description", required=True),
        debit=parse_money(data.get("debit"), f"{path}.debit"),
        credit=parse_money(data.get("credit"), f"{path}.credit"),
        balance=parse_money(data.get("balance"), f"{path}.balance"),
        value_date=_parse_date(data.get("value_date"), f"{path}.value_date"),
        reference=_parse_str(data.get("reference"), f"{path}.reference"),
        page=_parse_int(data.get("page"), f"{path}.page"),
    )


def statement_from_dict(data):
    """Build a Statement from its serialized (JSON-compatible) form."""
    if not isinstance(data, dict):
        raise SchemaError(f"statement: expected object, got {type(data).__name__}")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise SchemaError(f"statement.schema_version: unsupported version {version!r}")

    h = data.get("header") or {}
    if not isinstance(h, dict):
        raise SchemaError("statement.header: expected object")
    header = StatementHeader(
        bank=_parse_str(h.get("bank"), "header.bank"),
        account_holder_name=_parse_str(h.get("account_holder_name"), "header.account_holder_name"),
        account_number=_parse_str(h.get("account_number"), "header.account_number"),
        ifsc=_parse_str(h.get("ifsc"), "header.ifsc"),
        branch=_parse_str(h.get("branch"), "header.branch"),
        currency=_parse_str(h.get("currency"), "header.currency"),
        period_start=_parse_date(h.get("period_start"), "header.period_start"),
        period_end=_parse_date(h.get("period_end"), "header.period_end"),
        opening_balance=parse_money(h.get("opening_balance"), "header.opening_balance"),
        closing_balance=parse_money(h.get("closing_balance"), "header.closing_balance"),
    )

    raw_txns = data.get("transactions")
    if not isinstance(raw_txns, list):
        raise SchemaError("statement.transactions: expected list")
    txns = tuple(transaction_from_dict(t, f"transactions[{i}]") for i, t in enumerate(raw_txns))
    return Statement(header=header, transactions=txns, schema_version=version)
