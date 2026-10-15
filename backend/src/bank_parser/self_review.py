"""
The system's own review of one extraction (Stage 10.4, D-024).

Deterministic: built only from evidence the code has (the validation report,
which rule found each account field, how the template was learned), so it
never claims a check that did not run. It uses field names, counts, row
numbers and check codes only, never statement values, so it is safe to show
and store.

Confidence per field:
  high     an independent check confirms it (balance chain, reconciliation,
           every transaction date inside the period, IFSC pattern)
  medium   a rule extracted it, but nothing independent confirms it
  low      a check questioned it
  missing  not extracted
"""
from bank_parser.extraction.generic_rules import GENERIC_HEADER_RULES

HEADER_FIELDS = ("bank_name", "account_holder_name", "account_holder_address", "account_number", "ifsc",
                 "branch", "bank_address", "currency", "period_start", "period_end",
                 "opening_balance", "closing_balance")
TEXT_FIELDS = ("account_holder_name", "account_holder_address", "branch", "bank_address", "currency")
WORTH_A_LOOK = ("account_holder_name", "account_holder_address", "branch", "bank_address")   # listed under "unsure"
MONEY_COLUMNS = ("debit", "credit", "balance")
ROW_PROBLEMS = {"balance_mismatch": "its balance does not follow from the previous row",
                "malformed_amount": "an amount could not be read",
                "missing_amount": "it has neither a debit nor a credit",
                "negative_amount": "it has a negative amount",
                "both_debit_and_credit": "it has both a debit and a credit",
                "missing_date": "it has no date",
                "date_out_of_order": "its date is out of order",
                "date_outside_period": "its date is outside the statement period"}
MADE_BY = {"human_review": "by hand, after the owner's review", "template_agent": "the template agent"}
PROPOSERS = {"heuristic": "rules", "llm": "the local AI model"}
ROLE_SOURCES = {"header_text": "from the ruled table's heading words", "content": "from the columns' content",
                "content+chain": "from content, debit/credit decided by the balance chain",
                "heading_words": "from familiar heading words (no ruled lines)",
                "llm": "by the local AI model (no ruled lines)"}


def _checks(report):
    return {c["check"]: c for c in (report or {}).get("checks", [])}


def _codes(check):
    return [i["code"] for i in (check or {}).get("issues", [])]


def _rows_with(check, codes):
    return sorted({i["row_index"] for i in (check or {}).get("issues", [])
                   if i["code"] in codes and i.get("row_index") is not None})


def _item(items, id_, field, text, level="medium"):
    items.append({"id": id_, "field": field, "text": text, "level": level})


def _source_text(target, sources, template):
    """How a header field was found, from the rule that filled it."""
    if sources is None:
        return "medium", "source not recorded (extracted before self-reviews existed)", False
    src = sources.get(f"header.{target}")
    if src is None:
        return "medium", "taken from the layout", False
    rule = None
    if template is not None and src["rule"] < len(template.header_rules):
        rule = template.header_rules[src["rule"]]
    generic = rule is not None and rule in GENERIC_HEADER_RULES
    how = {"regex": "found next to its label", "block": "found next to its label (multi-line)",
           "region": "taken from a position on the page, not next to a label"}.get(src["kind"], src["kind"])
    if generic:
        how += " by a general rule, not one learned for this layout"
    return "medium", f"{how} (page {src['page']})", src["kind"] == "region" or generic


def build_self_review(statement, report, field_sources=None, template=None, job=None):
    """The self-review as a JSON-ready dict. `statement` and `report` are their stored dict forms."""
    statement = statement or {}
    header = statement.get("header") or {}
    txns = statement.get("transactions") or []
    summary = statement.get("summary") or {}
    checks = _checks(report)
    chain, recon, rows, head = (checks.get(k) for k in ("balance_chain", "reconciliation", "rows", "header"))
    checked, unsure, fields = [], [], {}
    n = len(txns)

    # --- money: the balance chain ---------------------------------------------------------
    chain_codes = _codes(chain)
    bad_money = _rows_with(chain, ("balance_mismatch", "malformed_amount", "missing_amount", "negative_amount"))
    bad_rows = _rows_with(rows, ("both_debit_and_credit",))
    verified_rows = (chain or {}).get("rows_checked", 0)
    if chain and chain["status"] in ("PASS", "WARN") and verified_rows:
        checked.append(f"Balance chain: each row's balance follows from the previous one on {verified_rows} of {n} rows.")
        money = ("high", "verified by the balance chain") if not bad_money else ("low", f"{len(bad_money)} row(s) do not add up")
    elif chain and chain["status"] == "FAIL":
        money = ("low", f"the balance chain fails on {len(bad_money)} row(s)")
    else:
        money = ("medium", "no running balance to check against")
        _item(unsure, "money:unchecked", "transactions", "Amounts could not be checked: the statement prints no running balance.")
    for col in MONEY_COLUMNS:
        fields[f"transactions.{col}"] = {"level": money[0], "reason": money[1]}
    if "no_opening_balance" in chain_codes and n:
        # The row that cannot be checked is the oldest one: the last printed on newest-first statements.
        dates = [t.get("txn_date") for t in txns if t.get("txn_date")]
        oldest = txns[-1] if len(dates) >= 2 and dates[0] > dates[-1] else txns[0]
        r = oldest.get("row_index", 0)
        _item(unsure, f"row:{r}:no_opening_balance", f"txn.{r}",
              f"Row {r + 1}'s balance is taken as printed: the statement prints no opening balance, so its oldest "
              "row cannot be checked against one.")
    for r in sorted(set(bad_money) | set(bad_rows)):
        codes = [i["code"] for i in (chain or {}).get("issues", []) + (rows or {}).get("issues", [])
                 if i.get("row_index") == r and i["code"] in ROW_PROBLEMS]
        _item(unsure, f"row:{r}:money", f"txn.{r}",
              f"Row {r + 1}: " + "; ".join(ROW_PROBLEMS[c] for c in dict.fromkeys(codes)) + ".", "low")

    # --- dates -----------------------------------------------------------------------------
    date_rows = _rows_with(rows, ("missing_date", "date_out_of_order", "date_outside_period"))
    if rows and n:
        if not date_rows:
            checked.append(f"Dates: all {n} rows have a date, in order"
                           + (", inside the statement period." if header.get("period_start") and header.get("period_end") else "."))
            fields["transactions.txn_date"] = {"level": "high", "reason": "every row dated, in order"}
        else:
            fields["transactions.txn_date"] = {"level": "low", "reason": f"{len(date_rows)} row(s) questioned"}
            for r in date_rows:
                if not any(u["id"] == f"row:{r}:money" for u in unsure):
                    codes = [i["code"] for i in rows["issues"] if i.get("row_index") == r]
                    _item(unsure, f"row:{r}:date", f"txn.{r}",
                          f"Row {r + 1}: " + "; ".join(ROW_PROBLEMS[c] for c in dict.fromkeys(codes) if c in ROW_PROBLEMS) + ".",
                          "low")
    fields["transactions.value_date"] = {"level": "medium", "reason": "read from its column; no check uses it"}

    # --- text columns: nothing arithmetic checks them --------------------------------------
    for col in ("description", "reference"):
        fields[f"transactions.{col}"] = {"level": "medium", "reason": "text: no arithmetic check can confirm it"}
    if n:
        _item(unsure, "text:descriptions", "transactions",
              "Descriptions and references are text: no arithmetic check can confirm them. Look for words cut off, "
              "joined to the wrong row, or missing.")

    # --- reconciliation with printed totals -------------------------------------------------
    recon_codes = _codes(recon)
    compared = []
    latest_balance = any(t.get("balance") is not None for t in txns)
    if header.get("closing_balance") is not None and latest_balance:
        compared.append(("closing balance", "closing_balance_mismatch", "header.closing_balance"))
    if header.get("closing_balance") is not None and header.get("opening_balance") is not None:
        compared.append(("opening balance + movements = closing balance", "net_movement_mismatch", "header.opening_balance"))
    for key, code, label in (("total_debits", "total_debits_mismatch", "total debits"),
                             ("total_credits", "total_credits_mismatch", "total credits"),
                             ("debit_count", "debit_count_mismatch", "number of debits"),
                             ("credit_count", "credit_count_mismatch", "number of credits")):
        if summary.get(key) is not None:
            compared.append((label, code, f"summary.{key}"))
    ok = [label for label, code, _ in compared if code not in recon_codes]
    if ok:
        checked.append("Matches the bank's printed figures: " + ", ".join(ok) + ".")
    for label, code, path in compared:
        if code in recon_codes:
            _item(unsure, f"recon:{code}", path, f"The extracted {label} does not match the figure the bank printed.", "low")
    for key in ("total_debits", "total_credits", "debit_count", "credit_count"):
        if summary.get(key) is not None:
            bad = f"{key}_mismatch" in recon_codes
            fields[f"summary.{key}"] = {"level": "low" if bad else "high",
                                        "reason": "does not match the transactions" if bad else "matches the transactions"}

    # --- account fields ----------------------------------------------------------------------
    head_codes = _codes(head)
    for name in HEADER_FIELDS:
        value = header.get(name)
        if value in (None, ""):
            fields[f"header.{name}"] = {"level": "missing", "reason": "not extracted"}
            continue
        level, reason, flag = _source_text(name, field_sources, template)
        if name == "ifsc":
            level, reason = ("low", "does not match the IFSC pattern") if "invalid_ifsc" in head_codes else \
                ("high", "matches the IFSC pattern (AAAA0XXXXXX); " + reason)
        elif name == "account_number":
            level, reason = ("low", "is not 6-20 digits") if "invalid_account_number" in head_codes else \
                ("medium", "format checked (6-20 digits); " + reason)
        elif name in ("period_start", "period_end"):
            outside = "date_outside_period" in _codes(rows)
            level, reason = ("low", "some transaction dates fall outside it") if outside or "period_reversed" in head_codes \
                else ("high", "every transaction date lies inside the period")
        elif name == "closing_balance":
            hit = [c for _, c, p in compared if p == "header.closing_balance"]
            if hit:
                level, reason = ("low", "does not match the last row's balance") if hit[0] in recon_codes \
                    else ("high", "equals the last row's balance")
        elif name == "opening_balance":
            if chain and verified_rows and "no_opening_balance" not in chain_codes and not bad_money:
                level, reason = "high", "the first row's balance follows from it"
        elif name == "bank_name":
            reason = "from the layout and the IFSC bank code"
        fields[f"header.{name}"] = {"level": level, "reason": reason}
        if flag and name in WORTH_A_LOOK:
            _item(unsure, f"field:header.{name}", f"header.{name}", f"{name.replace('_', ' ').capitalize()}: {reason}.")
    if any(fields[f"header.{k}"]["level"] == "medium" for k in TEXT_FIELDS if f"header.{k}" in fields):
        checked.append("Account details: read by this layout's rules; text fields cannot be checked by arithmetic.")

    # --- how the layout was learned -----------------------------------------------------------
    learning = None
    prov = (template.provenance if template is not None else {}) or {}
    if prov.get("created_by") == "template_agent":
        proposer = prov.get("proposer")
        learning = {"template": template.template_id, "proposer": PROPOSERS.get(proposer, proposer),
                    "columns": ROLE_SOURCES.get(prov.get("role_source"), prov.get("role_source")),
                    "model": prov.get("model"), "swapped": bool(prov.get("swapped")),
                    "attempts": len((job or {}).get("attempts") or []) or None,
                    "accepted_because": (job or {}).get("reason") if (job or {}).get("status") == "ACCEPTED" else None}
        if proposer == "llm":
            _item(unsure, "learning:llm_columns", "transactions",
                  f"The columns were named by the local AI model ({prov.get('model')}); the content checks and the "
                  "balance chain accepted its labels.")
        if prov.get("swapped"):
            _item(unsure, "learning:swap", "transactions",
                  "Debit and credit were told apart by the balance chain (the first labelling had them the other way round).")
    elif template is not None:
        learning = {"template": template.template_id, "proposer": None,
                    "made_by": MADE_BY.get(prov.get("created_by"), prov.get("created_by") or "not recorded"),
                    "columns": None}
    else:
        learning = {"template": None, "proposer": "rules (one-off reading of a new layout)", "columns": None}

    things = len(unsure)
    head_line = (f"Money verified on {verified_rows} of {n} rows" if verified_rows else
                 "Money could not be verified" if n else "No transactions extracted")
    return {"version": 1, "summary": f"{head_line}; {things} thing{'s' if things != 1 else ''} to check.",
            "checked": checked, "unsure": unsure, "fields": fields, "learning": learning}


def calibration(self_review, review):
    """Compare the self-review's confidence with the owner's marks: where it was too sure or too careful."""
    if not self_review or not review:
        return None
    fields = self_review.get("fields", {})
    wrong = set(review.get("flags", [])) | set(review.get("missing", []))
    sure_but_wrong = sorted(k for k in wrong if fields.get(k, {}).get("level") == "high")
    rows_wrong = sorted(int(k[4:]) for k in wrong if k.startswith("txn."))
    money_high = all(fields.get(f"transactions.{c}", {}).get("level") == "high" for c in MONEY_COLUMNS)
    flagged_rows = {u["field"] for u in self_review.get("unsure", []) if u["field"].startswith("txn.")}
    return {"wrong_but_rated_high": sure_but_wrong,
            "rows_marked_wrong": len(rows_wrong),
            "rows_marked_wrong_not_flagged": sorted(r for r in rows_wrong if f"txn.{r}" not in flagged_rows),
            "money_rated_high": money_high,
            "feedback": len(review.get("agent_feedback") or {})}
