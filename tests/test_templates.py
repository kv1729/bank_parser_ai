import json
from dataclasses import replace

import pytest

from bank_parser.detection import detect, detection_text
from bank_parser.learning.markers import LABEL_VOCABULARY, choose_markers
from bank_parser.pdf import open_pdf
from bank_parser.templates import TemplateError, TemplateRegistry, VersionConflict, template_from_dict, template_to_dict
from tests.helpers import COMMITTED_TEMPLATES, HDFC_LAYOUT, HDFC_PDF, SBI_LAYOUT, SBI_PDF


@pytest.fixture
def committed():
    reg = TemplateRegistry([COMMITTED_TEMPLATES])
    assert reg.load_errors == []
    return reg


def test_committed_templates_load_and_round_trip(committed):
    assert {t.template_id for t in committed.all()} >= {f"{SBI_LAYOUT}/v1", f"{HDFC_LAYOUT}/v1", f"{HDFC_LAYOUT}/v2"}
    for t in committed.all():
        assert template_from_dict(json.loads(json.dumps(template_to_dict(t)))) == t


def test_versions_are_never_overwritten(committed, tmp_path):
    reg = TemplateRegistry([], tmp_path)
    t = committed.get(f"{SBI_LAYOUT}/v1")
    path = reg.save(t)
    original = path.read_bytes()
    with pytest.raises(VersionConflict):
        reg.save(replace(t, markers=("Different",)))
    assert path.read_bytes() == original
    assert reg.next_version(t.bank_code, t.layout_id) == 2
    reg.save(t.with_version(2))
    assert reg.versions(t.bank_code, t.layout_id) == [1, 2]


def test_exclusive_create_even_if_registry_is_stale(committed, tmp_path):
    a, b = TemplateRegistry([], tmp_path), TemplateRegistry([], tmp_path)   # two workers
    t = committed.get(f"{SBI_LAYOUT}/v1")
    a.save(t)
    with pytest.raises(VersionConflict):
        b.save(t)            # b has not reloaded, but the file system refuses the overwrite


def test_misplaced_template_file_is_rejected(committed, tmp_path):
    t = committed.get(f"{SBI_LAYOUT}/v1")
    wrong = tmp_path / "sbi" / "other-layout"
    wrong.mkdir(parents=True)
    (wrong / "v1.json").write_text(json.dumps(template_to_dict(t)), encoding="utf-8")
    reg = TemplateRegistry([tmp_path])
    assert reg.all() == [] and reg.load_errors


@pytest.mark.parametrize("change", [{"format": 99}, {"strategy": "llm_magic"}, {"version": 0}])
def test_invalid_templates_rejected(committed, change):
    d = template_to_dict(committed.get(f"{SBI_LAYOUT}/v1"))
    d.update(change)
    with pytest.raises(TemplateError):
        template_from_dict(d)


def test_unknown_header_target_rejected(committed):
    d = template_to_dict(committed.get(f"{SBI_LAYOUT}/v1"))
    d["header_rules"][0]["targets"] = ["header.password"]
    with pytest.raises(TemplateError):
        template_from_dict(d)


@pytest.mark.parametrize("path, layout", [(SBI_PDF, SBI_LAYOUT), (HDFC_PDF, HDFC_LAYOUT)])
def test_detection_picks_own_layout_only(committed, path, layout):
    with open_pdf(path) as doc:
        matches = detect(detection_text(doc), committed.all())
    assert matches and all(m.template.template_id.startswith(layout) for m in matches)
    assert matches[0].template.version == max(m.template.version for m in matches)   # newest first


def test_markers_contain_only_label_vocabulary(committed):
    for t in committed.all():
        for marker in t.markers:
            assert all(w.strip("()").lower() in LABEL_VOCABULARY for w in marker.split())


def test_markers_never_include_personal_values():
    text = "Mr. Some Person My Address : 12 Main Road\nAccount Number : 1234567890\nIFSC Code : ABCD0123456"
    markers = choose_markers(text, forbidden_values=["Mr. Some Person"])
    assert "Account Number" in markers and "IFSC Code" in markers
    assert not any("Person" in m or "Some" in m or "Main" in m for m in markers)
