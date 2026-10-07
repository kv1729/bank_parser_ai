"""scripts/export_extraction.py: read-only CSV export of stored extractions."""
import csv
import importlib.util

from bank_parser.config import Settings
from bank_parser.pipeline import Processor
from tests.helpers import COMMITTED_TEMPLATES, ROOT, SBI_PDF


def _load_script():
    spec = importlib.util.spec_from_file_location("export_extraction", ROOT / "scripts" / "export_extraction.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_latest_extraction_to_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BANK_PARSER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BANK_PARSER_TEMPLATES_DIR", str(COMMITTED_TEMPLATES))
    p = Processor(Settings.from_env(), learning_mode="thread")
    with open(SBI_PDF, "rb") as f:
        doc_id = p.ingest(f.read, SBI_PDF.name).document_id
    p.process(doc_id)
    p.shutdown()

    script = _load_script()
    script.main(["--list"])
    assert doc_id[:12] in capsys.readouterr().out
    script.main(["--doc", doc_id[:8]])

    exports = tmp_path / "data" / "exports"
    txns = list(csv.DictReader(open(next(exports.glob("*_transactions.csv")), encoding="utf-8-sig")))
    header = {r["field"]: r["value"] for r in csv.DictReader(open(next(exports.glob("*_header.csv")), encoding="utf-8-sig"))}
    assert len(txns) == 32 and txns[0]["debit"] == "88.50" and txns[0]["direction"] == "DEBIT"
    assert header["header.ifsc"] == "SBIN0003606" and header["validation.status"] == "PASS"
