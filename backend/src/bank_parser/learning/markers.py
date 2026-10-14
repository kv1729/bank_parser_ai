"""
Privacy-safe layout markers.

Markers identify a layout in the template registry, and templates may be
committed to git, so a marker must never contain personal data. Markers are
built only from field *labels* ("Account Number", "IFSC Code"): the words
before a colon, keeping only words from a fixed vocabulary of statement
terms. Values, names and addresses can therefore never become markers.
"""
import re

LABEL_VOCABULARY = frozenset("""
a/c account accounts acct address amount as at available balance bank branch brought cheque chq cif city
clear closing code count credit credits currency customer cust date debit debits deposit description details
drawing email forward from home id ifs ifsc interest jointholders joint holders kyc limit micr mobile mod
name narration no nomination nominee number od of on open opening outstanding p.a. period phone power
product rate ref registered relationship segment state statement status summary to total transaction
transactions type value withdrawal withdrawals deposits remarks particulars inr usd sr
""".split())

_COLON_SPLIT = re.compile(r"\s*:\s*")
_WORD = re.compile(r"[A-Za-z][A-Za-z./()%]*")

MAX_MARKERS = 6
MIN_MARKERS = 2


def _label_before(segment):
    """Trailing run of vocabulary words in the text before a colon."""
    words = segment.split()
    picked = []
    for w in reversed(words):
        core = w.strip("()").lower()
        if not _WORD.fullmatch(w) or core not in LABEL_VOCABULARY:
            break
        picked.append(w)
        if len(picked) == 4:
            break
    return " ".join(reversed(picked))


def candidate_labels(text):
    labels = []
    for line in text.splitlines():
        parts = _COLON_SPLIT.split(line)
        for seg in parts[:-1]:
            label = _label_before(seg)
            if label and label not in labels:
                labels.append(label)
    return labels


def _vocabulary_only(label):
    words = label.split()
    return bool(words) and all(w.strip("().:").lower() in LABEL_VOCABULARY for w in words)


def heading_runs(text, header_tokens):
    """
    Runs of >= 2 consecutive vocabulary words on the printed column-header lines, as they
    appear in the page text. Header cells often wrap ("Withdrawal / Amount (INR)"), so a cell's
    label is not contiguous in the text; runs taken from the text lines always are.
    """
    tokens = set(header_tokens)
    runs = []
    for line in text.splitlines():
        words = line.split()
        if not words or not all(w in tokens for w in words):
            continue
        current = []
        for w in words + [None]:
            if w is not None and w.strip("().:").lower() in LABEL_VOCABULARY:
                current.append(w)
                continue
            if len(current) >= 2:
                runs.append(" ".join(current))
            current = []
    return runs


def choose_markers(text, forbidden_values=(), heading_labels=()):
    """
    Pick up to MAX_MARKERS labels, preferring multi-word ones (more specific).
    `forbidden_values` (extracted names/addresses) are a second safety net.

    Column headings ("Transaction Date", "Withdrawal Amount (INR)") are used only when the
    page has fewer than MIN_MARKERS "Label :" markers, so layouts learned before keep exactly
    the same markers. Only headings made entirely of vocabulary words qualify (D-020).
    """
    forbidden = [v.lower() for v in forbidden_values if v]
    labels = [l for l in candidate_labels(text)
              if not any(w.lower() in f for f in forbidden for w in l.split() if len(w) > 3 and w.lower() not in LABEL_VOCABULARY)]
    multi = [l for l in labels if len(l.split()) >= 2]
    single = [l for l in labels if len(l.split()) == 1]
    markers = (multi + single)[:MAX_MARKERS]
    if len(markers) < MIN_MARKERS:
        headings = [h for h in dict.fromkeys(" ".join(h.split()) for h in heading_labels)
                    if _vocabulary_only(h) and len(h.split()) >= 2 and h not in markers]
        markers += headings[: MAX_MARKERS - len(markers)]
    return tuple(markers)
