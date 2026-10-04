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


@pytest.mark.skipif(len(BACKENDS) < 2, reason="needs both backends")
@pytest.mark.parametrize("path", [SBI_PDF, HDFC_PDF])
def test_backends_agree_on_word_positions(path):
    """Templates store x-coordinates, so both backends must place words alike."""
    with open_pdf(path, backend="pdfplumber") as a, open_pdf(path, backend="pymupdf") as b:
        for n in range(1, a.page_count + 1):
            wa = {(w.text, round(w.x0)) for w in a.page(n).words}
            wb = {(w.text, round(w.x0)) for w in b.page(n).words}
            overlap = len(wa & wb) / max(len(wa), 1)
            assert overlap > 0.95
