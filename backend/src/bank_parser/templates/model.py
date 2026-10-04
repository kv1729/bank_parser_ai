"""
A template is the versioned extraction contract for one bank layout
(DECISIONS.md D-009). It is pure data (JSON), executed by the deterministic
extraction engine; it never contains code.
"""
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

TEMPLATE_FORMAT = 1

COLUMN_ROLES = ("txn_date", "value_date", "description", "reference", "debit", "credit",
                "amount", "dr_cr", "balance", "ignore")
STRATEGIES = ("word_columns",)
STATUSES = ("approved", "candidate", "rejected")

# Targets a header rule may write to.
HEADER_TARGETS = (
    "header.bank_name", "header.bank_address", "header.account_holder_name",
    "header.account_holder_address", "header.account_number", "header.ifsc", "header.branch",
    "header.currency", "header.period_start", "header.period_end", "header.opening_balance",
    "header.closing_balance", "summary.total_debits", "summary.total_credits",
    "summary.debit_count", "summary.credit_count",
)


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Column:
    role: str
    x0: float
    x1: float
    label: str | None = None    # extra-field name when role == "ignore" but data is kept


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[Column, ...]
    date_formats: tuple[str, ...]
    header_tokens: tuple[str, ...]          # words of the column-header row
    min_header_tokens: int = 1
    header_on_every_page: bool = True
    empty_tokens: tuple[str, ...] = ("", "-")
    line_tolerance: float = 3.0
    # How a debit/credit is represented: "columns" (separate debit/credit),
    # "signed" (one amount column, negative = debit) or "dr_cr" (amount + Dr/Cr marker).
    amount_mode: str = "columns"


@dataclass(frozen=True)
class HeaderRule:
    """
    kind == "regex": `pattern` is searched in the text of `pages`; group i is
    written to targets[i-1].
    kind == "region": lines between `after` and `before` anchors (regexes),
    restricted to words with x in [x_min, x_max]; `lines` = [start, stop) slice.
    """
    kind: str
    targets: tuple[str, ...]
    pages: tuple[int, ...] = (1,)            # negative = from the end (-1 = last page)
    pattern: str | None = None
    flags: str = ""                          # "i" ignorecase, "s" dotall, "m" multiline
    after: str | None = None
    before: str | None = None
    x_min: float = 0.0
    x_max: float = 10_000.0
    lines: tuple[int, int | None] = (0, None)
    strip: str | None = None                 # regex removed from each region line (e.g. labels)
    join: str = ", "
    date_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class Template:
    bank_code: str
    bank_name: str
    layout_id: str
    version: int
    markers: tuple[str, ...]                 # all must appear on the first pages (whole-phrase match)
    table: TableSpec
    header_rules: tuple[HeaderRule, ...] = ()
    status: str = "approved"
    strategy: str = "word_columns"
    required_checks: tuple[str, ...] = ("balance_chain",)
    provenance: dict = field(default_factory=dict)    # created_by, created_at, source_document_id, notes
    regression: dict = field(default_factory=dict)    # status, run_at, documents
    format: int = TEMPLATE_FORMAT

    @property
    def template_id(self):
        return f"{self.bank_code}/{self.layout_id}/v{self.version}"

    def with_version(self, version):
        return replace(self, version=version)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- (de)serialization ---------------------------------------------------------

def _req(d, key, path):
    if key not in d:
        raise TemplateError(f"{path}.{key}: required")
    return d[key]


def template_from_dict(d):
    if not isinstance(d, dict):
        raise TemplateError("template: expected object")
    if d.get("format") != TEMPLATE_FORMAT:
        raise TemplateError(f"template.format: unsupported {d.get('format')!r}")
    t = _req(d, "table", "template")
    cols = tuple(Column(c["role"], float(c["x0"]), float(c["x1"]), c.get("label")) for c in _req(t, "columns", "table"))
    for c in cols:
        if c.role not in COLUMN_ROLES:
            raise TemplateError(f"table.columns: unknown role {c.role!r}")
        if c.x0 >= c.x1:
            raise TemplateError(f"table.columns: column {c.role} has x0 >= x1")
    roles = [c.role for c in cols]
    if "txn_date" not in roles:
        raise TemplateError("table.columns: a txn_date column is required")
    table = TableSpec(
        columns=cols,
        date_formats=tuple(_req(t, "date_formats", "table")),
        header_tokens=tuple(t.get("header_tokens", ())),
        min_header_tokens=int(t.get("min_header_tokens", 1)),
        header_on_every_page=bool(t.get("header_on_every_page", True)),
        empty_tokens=tuple(t.get("empty_tokens", ("", "-"))),
        line_tolerance=float(t.get("line_tolerance", 3.0)),
        amount_mode=t.get("amount_mode", "columns"),
    )
    rules = []
    for r in d.get("header_rules", ()):
        targets = tuple(r["targets"])
        for tg in targets:
            if tg not in HEADER_TARGETS and not tg.startswith("header.extra."):
                raise TemplateError(f"header_rules: unknown target {tg!r}")
        rules.append(HeaderRule(
            kind=r["kind"], targets=targets, pages=tuple(r.get("pages", (1,))), pattern=r.get("pattern"),
            flags=r.get("flags", ""), after=r.get("after"), before=r.get("before"),
            x_min=float(r.get("x_min", 0.0)), x_max=float(r.get("x_max", 10_000.0)),
            lines=tuple(r.get("lines", (0, None))), strip=r.get("strip"), join=r.get("join", ", "),
            date_formats=tuple(r.get("date_formats", ())),
        ))
    tpl = Template(
        bank_code=_req(d, "bank_code", "template"), bank_name=_req(d, "bank_name", "template"),
        layout_id=_req(d, "layout_id", "template"), version=int(_req(d, "version", "template")),
        markers=tuple(_req(d, "markers", "template")), table=table, header_rules=tuple(rules),
        status=d.get("status", "approved"), strategy=d.get("strategy", "word_columns"),
        required_checks=tuple(d.get("required_checks", ("balance_chain",))),
        provenance=dict(d.get("provenance", {})), regression=dict(d.get("regression", {})),
    )
    if tpl.strategy not in STRATEGIES:
        raise TemplateError(f"template.strategy: unknown {tpl.strategy!r}")
    if tpl.status not in STATUSES:
        raise TemplateError(f"template.status: unknown {tpl.status!r}")
    if tpl.version < 1:
        raise TemplateError("template.version must be >= 1")
    return tpl


def template_to_dict(t):
    return {
        "format": t.format,
        "template_id": t.template_id,
        "bank_code": t.bank_code,
        "bank_name": t.bank_name,
        "layout_id": t.layout_id,
        "version": t.version,
        "status": t.status,
        "strategy": t.strategy,
        "markers": list(t.markers),
        "table": {
            "columns": [{k: v for k, v in (("role", c.role), ("x0", round(c.x0, 2)), ("x1", round(c.x1, 2)),
                                           ("label", c.label)) if v is not None} for c in t.table.columns],
            "date_formats": list(t.table.date_formats),
            "header_tokens": list(t.table.header_tokens),
            "min_header_tokens": t.table.min_header_tokens,
            "header_on_every_page": t.table.header_on_every_page,
            "empty_tokens": list(t.table.empty_tokens),
            "line_tolerance": t.table.line_tolerance,
            "amount_mode": t.table.amount_mode,
        },
        "header_rules": [_rule_to_dict(r) for r in t.header_rules],
        "required_checks": list(t.required_checks),
        "provenance": dict(t.provenance),
        "regression": dict(t.regression),
    }


def _rule_to_dict(r):
    d = {"kind": r.kind, "targets": list(r.targets), "pages": list(r.pages)}
    defaults = HeaderRule(kind=r.kind, targets=r.targets)
    for name in ("pattern", "flags", "after", "before", "x_min", "x_max", "strip", "join"):
        v = getattr(r, name)
        if v != getattr(defaults, name):
            d[name] = v
    if r.lines != defaults.lines:
        d["lines"] = list(r.lines)
    if r.date_formats:
        d["date_formats"] = list(r.date_formats)
    return d
