import pytest

from bank_parser.pdf import MalformedPdf, PasswordRequired, PdfKind, classify, open_pdf
from tests.helpers import BACKENDS, HDFC_PDF, ROOT, SBI_PDF, have_pymupdf


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("path, pages", [(SBI_PDF, 2), (HDFC_PDF, 1)])
def test_text_pdfs_classified(backend, path, pages):
    c = classify(path, backend=backend)
    assert c.kind is PdfKind.TEXT
    assert c.page_count == pages
    assert c.needs_ocr_pages == ()


@pytest.mark.parametrize("backend", BACKENDS)
def test_non_pdf_is_malformed(backend):
    with pytest.raises(MalformedPdf):
        classify(ROOT / "pyproject.toml", backend=backend)


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build test PDFs")
@pytest.mark.parametrize("backend", BACKENDS)
def test_encrypted_pdf_requires_password(tmp_path, backend):
    import pymupdf
    doc = pymupdf.open(SBI_PDF)
    out = tmp_path / "locked.pdf"
    doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="s3cret", owner_pw="owner")
    doc.close()
    for pw in (None, "wrong"):
        with pytest.raises(PasswordRequired):
            classify(out, pw, backend)
    assert classify(out, "s3cret", backend).kind is PdfKind.TEXT


@pytest.mark.skipif(not have_pymupdf(), reason="pymupdf needed to build test PDFs")
@pytest.mark.parametrize("backend", BACKENDS)
def test_image_only_pages_flagged_for_ocr(tmp_path, backend):
    import pymupdf
    doc = pymupdf.open(SBI_PDF)
    blank = doc.new_page()                     # page 3: no text layer
    blank.draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0.5, 0.5, 0.5))
    out = tmp_path / "mixed.pdf"
    doc.save(out)
    doc.close()
    c = classify(out, backend=backend)
    assert c.kind is PdfKind.MIXED
    assert c.needs_ocr_pages == (3,)


@pytest.mark.parametrize("other", [b for b in BACKENDS if b != "pdfplumber"])
@pytest.mark.parametrize("path", [SBI_PDF, HDFC_PDF])
def test_backends_agree_on_word_positions(path, other):
    """Templates store x-coordinates, so every backend must place words like pdfplumber."""
    with open_pdf(path, backend="pdfplumber") as a, open_pdf(path, backend=other) as b:
        for n in range(1, a.page_count + 1):
            wa = {(w.text, round(w.x0)) for w in a.page(n).words}
            wb = {(w.text, round(w.x0)) for w in b.page(n).words}
            overlap = len(wa & wb) / max(len(wa), 1)
            assert overlap > 0.95


def test_pypdfium2_keeps_line_end_hyphen_and_splits_there():
    # PDFium marks a hyphen at a cell's line end with U+FFFE and joins the next glyph
    # (often in another column) without a space. The backend must restore "-" and split.
    with open_pdf(SBI_PDF, backend="pypdfium2") as doc:
        words = [w.text for w in doc.page(1).words]
    assert "CLOSURE-" in words
    assert not any("￾" in w for w in words)
    assert not any(w.startswith("CLOSURE-") and len(w) > len("CLOSURE-") for w in words)


@pytest.mark.parametrize("path", [SBI_PDF, HDFC_PDF])
def test_pypdfium2_output_identical_to_pdfplumber(path):
    """D-015 acceptance: the default backend must produce exactly pdfplumber's statement."""
    from bank_parser.extraction.unknown import extract_unknown
    results = []
    for backend in ("pdfplumber", "pypdfium2"):
        with open_pdf(path, backend=backend) as doc:
            result, _ = extract_unknown(doc, path, None)
        s = result.statement
        results.append((s.header, s.summary, [(t.txn_date, t.value_date, t.description, t.reference, t.debit,
                                                t.credit, t.balance, t.page, t.extra) for t in s.transactions]))
    assert results[0] == results[1]


def test_pdfium_is_safe_under_concurrent_use():
    """Regression (D-018): concurrent page renders + parsing crashed the server process.
    Every PDFium call is serialised by PDFIUM_LOCK; this hammers it from many threads."""
    from concurrent.futures import ThreadPoolExecutor
    from bank_parser.pdf.render import render_page_png

    def render(i):
        return render_page_png(SBI_PDF, 1 + i % 2, scale=0.75)[:8]

    def parse(i):
        with open_pdf(HDFC_PDF if i % 2 else SBI_PDF, backend="pypdfium2") as doc:
            return sum(len(doc.page(n).words) for n in range(1, doc.page_count + 1))

    with ThreadPoolExecutor(12) as pool:
        renders = list(pool.map(render, range(48)))
        words = list(pool.map(parse, range(48)))
    assert all(r == b"\x89PNG\r\n\x1a\n" for r in renders)
    assert len(set(words[0::2])) == 1 and len(set(words[1::2])) == 1   # same result every time
