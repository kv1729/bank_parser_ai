"""
Unruled tables and the local-model labeller (Stage 10, D-023). No model server is
needed: a fake client returns canned answers, so these run in CI.
"""
from dataclasses import replace
import json
import re
from pathlib import Path

import pytest

from bank_parser.core.schema import statement_to_dict
from bank_parser.extraction.engine import extract_with_template
from bank_parser.golden import snapshot
from bank_parser.learning import LearningBudget, LearningStatus, TemplateAgent
from bank_parser.learning import llm_view
from bank_parser.learning.proposers import LLMProposer
from bank_parser.llm.ollama import LLMError, LLMReply, OllamaClient, require_local
from bank_parser.pdf import open_pdf
from bank_parser.pdf.json_layout import load_layout
from bank_parser.templates import TemplateRegistry, template_from_dict
from tests.helpers import SBI_PDF

BUDGET = LearningBudget(max_llm_tokens=50_000)
LAYOUTS = Path(__file__).parent / "fixtures" / "layouts"
SBI_ROLES = ["txn_date", "value_date", "description", "reference", "debit", "credit", "balance"]
CORE = ("txn_date", "value_date", "description", "reference", "debit", "credit", "balance")


class FakeClient:
    def __init__(self, answers, installed=True):
        self.answers, self.installed, self.calls, self.prompts = list(answers), installed, 0, []

    def has_model(self, model):
        return self.installed

    def chat_json(self, model, system, prompt, schema, seed=7, num_ctx=2048, max_tokens=200):
        self.calls += 1
        self.prompts.append(prompt)
        a = self.answers.pop(0) if self.answers else {}
        if isinstance(a, Exception):
            raise a
        return LLMReply(a, 1000, 100, 0.1)


class NoSaveRegistry(TemplateRegistry):
    def save(self, template):
        raise AssertionError("must not be saved")


@pytest.fixture
def registry(tmp_path):
    return TemplateRegistry([], tmp_path / "learned")


def answer(roles=SBI_ROLES):
    return {"roles": list(roles)}


def llm_only_agent(registry, client, budget=BUDGET):
    return TemplateAgent(registry, budget=budget, proposers=[LLMProposer(client, "test-model")])


# --- the agent with the model's labels --------------------------------------------------------

def test_correct_labels_become_a_verified_template(registry):
    client = FakeClient([answer()])
    out = llm_only_agent(registry, client).learn(SBI_PDF)
    assert out.status is LearningStatus.ACCEPTED, out.reason
    saved = registry.get(out.template_id)
    assert saved.provenance["proposer"] == "llm" and saved.provenance["model"] == "test-model"
    assert out.attempts[0]["llm_tokens"] == 1100


def test_the_model_sees_headings_and_samples_not_the_page():
    with open_pdf(SBI_PDF) as doc:
        view = llm_view.find_first_table(doc)
    prompt = llm_view.build_prompt(view)
    assert sum(1 for line in prompt.splitlines() if re.match(r"C\d+ heading", line)) == 7
    assert 'C5 heading "Debit"' in prompt
    assert "Account Number" not in prompt          # account details are not part of the table


def test_swapped_debit_credit_is_repaired_by_the_balance_chain_without_asking_again(registry):
    swapped = ["credit" if r == "debit" else "debit" if r == "credit" else r for r in SBI_ROLES]
    client = FakeClient([answer(swapped)])
    out = llm_only_agent(registry, client).learn(SBI_PDF)
    assert out.status is LearningStatus.ACCEPTED, out.reason
    assert [a["accepted"] for a in out.attempts] == [False, True]
    assert client.calls == 1


def test_labels_that_never_add_up_are_never_saved(tmp_path):
    wrong = ["txn_date", "value_date", "description", "reference", "debit", "balance", "credit"]
    client = FakeClient([answer(wrong), answer(wrong)])
    out = llm_only_agent(NoSaveRegistry([], tmp_path / "learned"), client).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW
    assert not any(a["accepted"] for a in out.attempts)
    assert client.calls == 2 and "do not add up" in client.prompts[1]   # one bounded re-ask


@pytest.mark.parametrize("bad", [
    {},                                                         # fields missing
    answer(SBI_ROLES[:6]),                                      # wrong number of roles
    answer(["debit"] * 7),                                      # a role used twice
    answer(["ignore"] + SBI_ROLES[1:]),                         # no transaction date
    answer(SBI_ROLES[:4] + ["amount", "credit", "balance"]),    # amount column next to a credit column
])
def test_unusable_answers_give_no_candidate(registry, bad):
    out = llm_only_agent(registry, FakeClient([bad, bad])).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and out.attempts == []


def test_model_errors_are_reported_not_raised(registry):
    out = llm_only_agent(registry, FakeClient([LLMError("down")])).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and "llm: down" in out.reason


def test_no_token_budget_means_no_model_call(registry):
    client = FakeClient([answer()])
    out = llm_only_agent(registry, client, LearningBudget()).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and client.calls == 0


def test_missing_model_means_proposer_unavailable(registry):
    client = FakeClient([answer()], installed=False)
    llm_only_agent(registry, client).learn(SBI_PDF)
    assert client.calls == 0
    assert not LLMProposer().available()


@pytest.mark.parametrize("url", ["http://example.com:11434", "http://10.0.0.5:11434", "https://api.example.org"])
def test_only_a_local_model_server_is_allowed(url):
    with pytest.raises(LLMError):
        require_local(url)
    with pytest.raises(LLMError):
        OllamaClient(url)


def test_local_urls_are_allowed():
    for url in ("http://127.0.0.1:11434", "http://localhost:11434/"):
        assert require_local(url) == url.rstrip("/")


# --- tables without ruled cells, on the anonymised real layouts ---------------------------------

def _copy(name):
    _, doc = load_layout(LAYOUTS / name / "layout.json")
    t = template_from_dict(json.loads((LAYOUTS / name / "template.json").read_text(encoding="utf-8")))
    out = json.loads((LAYOUTS / name / "output.json").read_text(encoding="utf-8"))["statement"]
    return doc, t, out


def _core(statement):
    return [tuple(str(r.get(k)) for k in CORE) for r in statement["transactions"]]


def _extract(doc, template, view, roles, mode):
    layout = llm_view.layout_from_roles(doc, view, roles, mode)
    res = extract_with_template(doc, replace(template, table=layout.spec), None, strategy="candidate")
    return snapshot(statement_to_dict(res.statement))


@pytest.mark.parametrize("name", ["boi-23f4f42a", "icici-ec27b806"])
def test_familiar_heading_words_rebuild_the_layout_without_a_model(name):
    doc, t, expected = _copy(name)
    view = llm_view.find_first_table(doc)
    roles = llm_view.word_roles(view)
    assert roles is not None
    assert roles[0] == [c.role for c in sorted(t.table.columns, key=lambda c: c.x0)]
    assert _core(_extract(doc, t, view, *roles)) == _core(expected)


def test_layout_without_heading_text_is_rebuilt_from_the_rows():
    """SBI prints only "Balance" above its columns: columns come from the rows, roles from a label."""
    doc, t, expected = _copy("sbi-d2e542a8")
    view = llm_view.find_first_table(doc)
    assert llm_view.word_roles(view) is None              # the model is needed here
    roles = [c.role for c in sorted(t.table.columns, key=lambda c: c.x0)]
    assert _core(_extract(doc, t, view, roles, "columns")) == _core(expected)


def test_description_lines_above_the_first_date_are_not_heading():
    doc, _, _ = _copy("icici-ec27b806")
    view = llm_view.find_first_table(doc)
    assert view.headings[3] == "Transaction Remarks" and len(view.headings) == 7


@pytest.mark.parametrize("swap", [("description", "reference"), ("txn_date", "description"), ("balance", "description")])
def test_labels_that_do_not_fit_the_values_are_rejected(swap):
    """The balance chain proves only money columns; text and date labels are checked against content."""
    doc, t, _ = _copy("icici-ec27b806")
    view = llm_view.find_first_table(doc)
    roles = [c.role for c in sorted(t.table.columns, key=lambda c: c.x0)]
    a, b = roles.index(swap[0]), roles.index(swap[1])
    roles[a], roles[b] = roles[b], roles[a]
    with pytest.raises(llm_view.AnswerError):
        llm_view.layout_from_roles(doc, view, roles, "columns")


# --- Workflow 1 on unruled tables (Stage 10.3): no model, extracted at once --------------------

@pytest.mark.parametrize("name", ["boi-23f4f42a", "icici-ec27b806"])
def test_workflow_1_reads_unruled_tables_with_familiar_headings(name):
    from bank_parser.extraction.unknown import extract_unruled
    doc, _, expected = _copy(name)
    result, report = extract_unruled(doc)
    assert result is not None
    assert _core(snapshot(statement_to_dict(result.statement))) == _core(expected)
    assert report.status.value == "WARN"          # no opening balance printed, as in the original


def test_workflow_1_leaves_tables_without_heading_words_to_the_agent():
    from bank_parser.extraction.unknown import extract_unruled
    doc, _, _ = _copy("sbi-d2e542a8")
    assert extract_unruled(doc) == (None, None)
