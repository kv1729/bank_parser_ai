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
