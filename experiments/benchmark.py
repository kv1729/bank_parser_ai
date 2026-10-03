"""
Latency benchmark per stage, per backend, known-template vs unknown-layout path.

    uv run python experiments/benchmark.py [--runs 5]

Synthetic samples always; the local real statement too when present
(sample_data/.pdf_password). Prints only timings and validation status --
never statement content.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bank_parser.detection import detect, detection_text          # noqa: E402
from bank_parser.extraction import extract_with_template         # noqa: E402
from bank_parser.extraction.unknown import extract_unknown        # noqa: E402
from bank_parser.learning import TemplateAgent                    # noqa: E402
from bank_parser.pdf import available_backends, classify, open_pdf  # noqa: E402
from bank_parser.templates import TemplateRegistry                # noqa: E402
from bank_parser.validation import validate_statement             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def documents():
    docs = [("synthetic SBI (2 pages)", ROOT / "sample_data/SBI_Bank_Statement_Chenna_Reddy.pdf", None),
            ("synthetic HDFC (1 page)", ROOT / "sample_data/sample_statement.pdf", None)]
    real, pw = ROOT / "sample_data/AccountStatement_02102026_120158.pdf", ROOT / "sample_data/.pdf_password"
    if real.exists() and pw.exists():
        docs.append(("real SBI (local only)", real, pw.read_text(encoding="utf-8").strip()))
    return docs


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000


def bench(path, password, backend, registry, tmp_registry_dir):
    row = {}
    _, row["classify"] = timed(lambda: classify(path, password, backend))
    with open_pdf(path, password, backend) as doc:
        text, row["detect"] = timed(lambda: detection_text(doc))
        matches = detect(text, registry.all())
        if matches:
            r, row["known_extract"] = timed(lambda: extract_with_template(doc, matches[0].template))
            rep, row["validate"] = timed(lambda: validate_statement(r.statement))
            row["known_status"] = rep.status.value
        (r2, rep2), row["unknown_extract"] = timed(lambda: extract_unknown(doc, path, password))
        row["unknown_status"] = rep2.status.value if rep2 else "NO_LAYOUT"
    agent = TemplateAgent(TemplateRegistry([], tmp_registry_dir), backend=backend)
    out, row["learn"] = timed(lambda: agent.learn(path, password))
    row["learn_status"] = out.status.value
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    import tempfile
    for name, path, password in documents():
        print(f"\n== {name}")
        for backend in available_backends():
            # The real statement has no committed template: learn one first so the known path can be timed.
            with tempfile.TemporaryDirectory() as tmp:
                registry = TemplateRegistry([ROOT / "template_registry"], Path(tmp) / "learned")
                if not detect(_text(path, password, backend), registry.all()):
                    TemplateAgent(registry, backend=backend).learn(path, password)
                runs = [bench(path, password, backend, registry, Path(tmp) / f"r{i}") for i in range(args.runs)]
            stages = ["classify", "detect", "known_extract", "validate", "unknown_extract", "learn"]
            parts = [f"{s} {statistics.median(r[s] for r in runs):7.1f}" for s in stages if s in runs[0]]
            known_total = sum(statistics.median(r[s] for r in runs) for s in ("classify", "detect", "known_extract", "validate"))
            print(f"  {backend:10s} median ms: " + " | ".join(parts))
            print(f"  {'':10s} known path total {known_total:7.1f} ms  status={runs[0].get('known_status')}; "
                  f"unknown path={runs[0]['unknown_status']}; learning={runs[0]['learn_status']}")


def _text(path, password, backend):
    with open_pdf(path, password, backend) as d:
        return detection_text(d)


if __name__ == "__main__":
    main()
