import pytest

from bank_parser.learning import LearningBudget, LearningStatus, RegressionDoc, TemplateAgent
from bank_parser.templates import TemplateRegistry
from tests.helpers import HDFC_LAYOUT, HDFC_PDF, SBI_PDF


@pytest.fixture
def registry(tmp_path):
    return TemplateRegistry([], tmp_path / "learned")


def test_agent_learns_and_saves_a_verified_template(registry):
    out = TemplateAgent(registry).learn(SBI_PDF)
    assert out.status is LearningStatus.ACCEPTED
    t = registry.get(out.template_id)
    assert t.version == 1 and t.bank_code == "sbi" and t.status == "approved"
    assert t.provenance["created_by"] == "template_agent"
    assert "balance_chain" in t.required_checks
    assert out.attempts and all(set(a) >= {"accepted", "validation", "rows"} for a in out.attempts)


def test_relearning_same_layout_creates_new_version(registry):
    first = TemplateAgent(registry).learn(SBI_PDF)
    second = TemplateAgent(registry).learn(SBI_PDF)
    assert second.status is LearningStatus.ACCEPTED
    assert first.template_id.endswith("/v1") and second.template_id.endswith("/v2")
    assert first.template_id.rsplit("/", 1)[0] == second.template_id.rsplit("/", 1)[0]
    assert registry.get(first.template_id) is not None        # v1 untouched


def test_agent_accepts_layout_without_opening_balance(registry):
    out = TemplateAgent(registry).learn(HDFC_PDF)
    assert out.status is LearningStatus.ACCEPTED


def test_ambiguous_markers_are_rejected_after_bounded_refinement(registry):
    # Pretend the same document belongs to another layout: the candidate's markers then
    # "hijack" it, refinement cannot fix that, and the agent must give up -- not loop.
    out = TemplateAgent(registry, [RegressionDoc(str(SBI_PDF), "other/layout")]).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW
    assert "markers" in out.reason
    assert len(out.attempts) <= LearningBudget().max_candidates + LearningBudget().max_refinements
    assert registry.all(include_unapproved=True) == []


def test_zero_budget_means_human_review(registry):
    out = TemplateAgent(registry, budget=LearningBudget(max_candidates=0, max_refinements=0)).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and out.attempts == []
    assert registry.all(include_unapproved=True) == []


def test_time_limit_is_enforced(registry):
    out = TemplateAgent(registry, budget=LearningBudget(max_seconds=0.0)).learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and "time limit" in out.reason


def test_regression_against_other_layouts(registry):
    out = TemplateAgent(registry, [RegressionDoc(str(HDFC_PDF), HDFC_LAYOUT)]).learn(SBI_PDF)
    assert out.status is LearningStatus.ACCEPTED
    assert "1 other-layout" in out.reason


# --- every proposer passes the same gates (CHECKPOINT D6, D-028) -----------------------------

def _proposers():
    from bank_parser.learning import llm_view
    from bank_parser.learning.examples import ExamplesProposer
    from bank_parser.learning.proposers import HeuristicProposer, LLMProposer
    from bank_parser.llm.ollama import LLMReply

    class Client:
        def has_model(self, model):
            return True

        def chat_json(self, *a, **k):
            return LLMReply({"roles": ["txn_date", "value_date", "description", "reference", "debit", "credit",
                                       "balance"]}, 10, 5, 0.1)

    class Examples(ExamplesProposer):        # labels the table as the familiar words would
        def propose(self, ctx):
            view = llm_view.find_first_table(ctx.doc)
            from bank_parser.learning.proposers import template_for
            layout = llm_view.layout_from_roles(ctx.doc, view, *llm_view.word_roles(view))
            yield template_for(ctx, layout.spec, self.name, {"role_source": "examples"})

        def available(self):
            return True

    return {"heuristic": HeuristicProposer(), "examples": Examples(),
            "llm": LLMProposer(Client(), "test-model")}


@pytest.mark.parametrize("name", ["heuristic", "examples", "llm"])
def test_every_proposer_is_refused_by_the_golden_gate(tmp_path, name):
    from bank_parser.learning.agent import LearningBudget as Budget

    class NoSave(TemplateRegistry):
        def save(self, template):
            raise AssertionError("a refused template must never be saved")

    refused = []

    def gate(templates):
        refused.append(len(templates))
        return False, "would change the output of 1 golden statement(s)"

    agent = TemplateAgent(NoSave([], tmp_path), budget=Budget(max_llm_tokens=10_000), proposers=[_proposers()[name]],
                          golden_check=gate)
    out = agent.learn(SBI_PDF)
    assert out.status is LearningStatus.HUMAN_REVIEW and refused
    assert out.attempts and all(a["proposer"] == name and not a["accepted"] for a in out.attempts)
    assert any(a["validation"] in ("PASS", "WARN") for a in out.attempts)    # it passed the self-check first


def test_learn_document_is_what_learn_runs(tmp_path):
    from bank_parser.pdf import open_pdf
    a = TemplateAgent(TemplateRegistry([], tmp_path / "a")).learn(SBI_PDF)
    with open_pdf(SBI_PDF) as doc, open_pdf(SBI_PDF, None, "pdfplumber") as cells:
        b = TemplateAgent(TemplateRegistry([], tmp_path / "b")).learn_document(doc, cells)
    assert (a.status, a.template_id, [x["columns"] for x in a.attempts]) == \
        (b.status, b.template_id, [x["columns"] for x in b.attempts])
