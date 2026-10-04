"""
Typed output contract for parsed bank statements.

Money is always Decimal. In serialized form (JSON) money is a plain decimal
string such as "120749.89" -- never a JSON number, so no value ever passes
through float.

Schema version 2 (DECISIONS.md D-008) adds addresses, printed summary totals,
provenance and open-ended `extra` fields. Version 1 documents still load.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import re

SCHEMA_VERSION = "2"
SUPPORTED_VERSIONS = ("1", "2")

_MONEY_RE = re.compile(r"^-?\d+(\.\d{1,2})?$")


class SchemaError(ValueError):
    """Raised when serialized statement data does not match the contract."""


class Direction(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


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
    # Any further columns the source provides (e.g. branch code, cheque no.).
    extra: dict = field(default_factory=dict)

    @property
    def direction(self):
        if self.debit is not None and self.credit is None:
            return Direction.DEBIT
        if self.credit is not None and self.debit is None:
            return Direction.CREDIT
        return None  # ambiguous or missing; validation reports it

    @property
    def amount(self):
        d = self.direction
        if d is Direction.DEBIT:
            return self.debit
        if d is Direction.CREDIT:
            return self.credit
        return None


@dataclass(frozen=True)
class StatementHeader:
    bank_name: str | None = None
    bank_address: str | None = None
    account_holder_name: str | None = None
    account_holder_address: str | None = None
    account_number: str | None = None
    ifsc: str | None = None
    branch: str | None = None
    currency: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StatementSummary:
    """Totals printed by the bank, used as independent validation evidence."""
    total_debits: Decimal | None = None
    total_credits: Decimal | None = None
    debit_count: int | None = None
    credit_count: int | None = None


@dataclass(frozen=True)
class Provenance:
    document_id: str | None = None
    strategy: str | None = None
    template_id: str | None = None
    template_version: int | None = None
    pdf_backend: str | None = None
    page_count: int | None = None
    extracted_at: datetime | None = None


@dataclass(frozen=True)
class Statement:
    header: StatementHeader
    transactions: tuple[Transaction, ...] = field(default_factory=tuple)
    summary: StatementSummary | None = None
    provenance: Provenance | None = None
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


def _parse_datetime(value, path):
    if value is None:
        return None
    if not isinstance(value, str):
        raise SchemaError(f"{path}: datetime must be an ISO string")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise SchemaError(f"{path}: invalid ISO datetime {value!r}") from None


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


def _parse_extra(value, path):
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise SchemaError(f"{path}: extra must be an object of string values")
    return dict(value)


def _obj(value, path):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SchemaError(f"{path}: expected object, got {type(value).__name__}")
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
        extra=_parse_extra(data.get("extra"), f"{path}.extra"),
    )


def statement_from_dict(data):
    """Build a Statement from its serialized (JSON-compatible) form."""
    if not isinstance(data, dict):
        raise SchemaError(f"statement: expected object, got {type(data).__name__}")
    version = data.get("schema_version")
    if version not in SUPPORTED_VERSIONS:
        raise SchemaError(f"statement.schema_version: unsupported version {version!r}")

    h = _obj(data.get("header"), "header")
    header = StatementHeader(
        # v1 called the bank field "bank"
        bank_name=_parse_str(h.get("bank_name", h.get("bank")), "header.bank_name"),
        bank_address=_parse_str(h.get("bank_address"), "header.bank_address"),
        account_holder_name=_parse_str(h.get("account_holder_name"), "header.account_holder_name"),
        account_holder_address=_parse_str(h.get("account_holder_address"), "header.account_holder_address"),
        account_number=_parse_str(h.get("account_number"), "header.account_number"),
        ifsc=_parse_str(h.get("ifsc"), "header.ifsc"),
        branch=_parse_str(h.get("branch"), "header.branch"),
        currency=_parse_str(h.get("currency"), "header.currency"),
        period_start=_parse_date(h.get("period_start"), "header.period_start"),
        period_end=_parse_date(h.get("period_end"), "header.period_end"),
        opening_balance=parse_money(h.get("opening_balance"), "header.opening_balance"),
        closing_balance=parse_money(h.get("closing_balance"), "header.closing_balance"),
        extra=_parse_extra(h.get("extra"), "header.extra"),
    )

    raw_txns = data.get("transactions")
    if not isinstance(raw_txns, list):
        raise SchemaError("statement.transactions: expected list")
    txns = tuple(transaction_from_dict(t, f"transactions[{i}]") for i, t in enumerate(raw_txns))

    summary = None
    if data.get("summary") is not None:
        s = _obj(data["summary"], "summary")
        summary = StatementSummary(
            total_debits=parse_money(s.get("total_debits"), "summary.total_debits"),
            total_credits=parse_money(s.get("total_credits"), "summary.total_credits"),
            debit_count=_parse_int(s.get("debit_count"), "summary.debit_count"),
            credit_count=_parse_int(s.get("credit_count"), "summary.credit_count"),
        )

    provenance = None
    if data.get("provenance") is not None:
        p = _obj(data["provenance"], "provenance")
        provenance = Provenance(
            document_id=_parse_str(p.get("document_id"), "provenance.document_id"),
            strategy=_parse_str(p.get("strategy"), "provenance.strategy"),
            template_id=_parse_str(p.get("template_id"), "provenance.template_id"),
            template_version=_parse_int(p.get("template_version"), "provenance.template_version"),
            pdf_backend=_parse_str(p.get("pdf_backend"), "provenance.pdf_backend"),
            page_count=_parse_int(p.get("page_count"), "provenance.page_count"),
            extracted_at=_parse_datetime(p.get("extracted_at"), "provenance.extracted_at"),
        )

    return Statement(header=header, transactions=txns, summary=summary,
                     provenance=provenance, schema_version=SCHEMA_VERSION)


# --- serialization -----------------------------------------------------------

def _money_out(v):
    return None if v is None else str(v)


def _date_out(v):
    return None if v is None else v.isoformat()


def transaction_to_dict(t):
    return {
        "row_index": t.row_index,
        "page": t.page,
        "txn_date": _date_out(t.txn_date),
        "value_date": _date_out(t.value_date),
        "description": t.description,
        "reference": t.reference,
        "debit": _money_out(t.debit),
        "credit": _money_out(t.credit),
        "balance": _money_out(t.balance),
        # derived, for consumers; ignored on load
        "amount": _money_out(t.amount),
        "direction": t.direction.value if t.direction else None,
        "extra": dict(t.extra),
    }


def statement_to_dict(s):
    h = s.header
    out = {
        "schema_version": SCHEMA_VERSION,
        "header": {
            "bank_name": h.bank_name,
            "bank_address": h.bank_address,
            "account_holder_name": h.account_holder_name,
            "account_holder_address": h.account_holder_address,
            "account_number": h.account_number,
            "ifsc": h.ifsc,
            "branch": h.branch,
            "currency": h.currency,
            "period_start": _date_out(h.period_start),
            "period_end": _date_out(h.period_end),
            "opening_balance": _money_out(h.opening_balance),
            "closing_balance": _money_out(h.closing_balance),
            "extra": dict(h.extra),
        },
        "transactions": [transaction_to_dict(t) for t in s.transactions],
        "summary": None,
        "provenance": None,
    }
    if s.summary is not None:
        out["summary"] = {
            "total_debits": _money_out(s.summary.total_debits),
            "total_credits": _money_out(s.summary.total_credits),
            "debit_count": s.summary.debit_count,
            "credit_count": s.summary.credit_count,
        }
    if s.provenance is not None:
        p = s.provenance
        out["provenance"] = {
            "document_id": p.document_id,
            "strategy": p.strategy,
            "template_id": p.template_id,
            "template_version": p.template_version,
            "pdf_backend": p.pdf_backend,
            "page_count": p.page_count,
            "extracted_at": p.extracted_at.isoformat() if p.extracted_at else None,
        }
    return out
