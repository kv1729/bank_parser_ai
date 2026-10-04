"""
Privacy gate for commits (run by .githooks/pre-commit and commit-msg).

Blocks a commit when staged files (or the commit message) contain:
  - forbidden paths: runtime data, databases, passwords, expected outputs,
    archify receipts, or any PDF other than the committed synthetic samples;
  - the PDF password (sample_data/.pdf_password);
  - any string in .git/info/sensitive-strings (local only, never committed;
    refresh with scripts/refresh_sensitive_strings.py);
  - email addresses or PAN-shaped strings not on the allowlist below.

Never prints the sensitive value itself -- only the file and which rule matched.

    python scripts/privacy_check.py                 # check staged files
    python scripts/privacy_check.py --message FILE  # check a commit message
"""
import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATHS = (
    "data/*", "*.db", "*.sqlite", "*.sqlite3", ".env", ".env.*",
    "sample_data/.pdf_password", "*.expected.json",
    "docs/diagrams/*.finalize.json", "docs/diagrams/*.finalize-summary.json",
    "docs/diagrams/*.delivery.json", "docs/diagrams/*.browser-check.json", "docs/diagrams/*/review-*/*",
)
ALLOWED_PDFS = {
    "sample_data/SBI_Bank_Statement_Chenna_Reddy.pdf",
    "sample_data/sample_statement.pdf",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
ALLOWED_EMAILS = {"noreply@anthropic.com"}   # Claude commit attribution
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org"}


def sensitive_strings():
    values = []
    pw = ROOT / "sample_data" / ".pdf_password"
    if pw.exists():
        values.append(("pdf password", pw.read_text(encoding="utf-8").strip()))
    listed = ROOT / ".git" / "info" / "sensitive-strings"
    if listed.exists():
        for line in listed.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and len(line) >= 4:
                values.append(("sensitive-strings entry", line))
    return [(label, v.lower()) for label, v in values if v]


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def staged_text(path):
    data = subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, check=True).stdout
    if b"\0" in data[:8000]:
        return None   # binary
    return data.decode("utf-8", errors="ignore")


def content_problems(text, where, needles):
    problems = []
    low = text.lower()
    for label, value in needles:
        if value in low:
            problems.append(f"{where}: contains a {label}")
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if email not in ALLOWED_EMAILS and email.split("@")[1] not in ALLOWED_EMAIL_DOMAINS:
            problems.append(f"{where}: contains an email address")
            break
    if PAN_RE.search(text):
        problems.append(f"{where}: contains a PAN-shaped string")
    return problems


def check_staged():
    needles = sensitive_strings()
    problems = []
    for path in staged_files():
        posix = path.replace("\\", "/")
        if any(fnmatch.fnmatch(posix, pat) for pat in FORBIDDEN_PATHS):
            problems.append(f"{posix}: forbidden path")
            continue
        if posix.lower().endswith(".pdf"):
            if posix not in ALLOWED_PDFS:
                problems.append(f"{posix}: PDF not on the allowlist (real statements must stay local)")
            continue
        text = staged_text(path)
        if text is not None:
            problems.extend(content_problems(text, posix, needles))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message", help="commit message file to check")
    args = ap.parse_args()
    if args.message:
        msg = Path(args.message).read_text(encoding="utf-8", errors="ignore")
        body = "\n".join(l for l in msg.splitlines() if not l.startswith("#"))
        problems = content_problems(body, "commit message", sensitive_strings())
    else:
        problems = check_staged()
    if problems:
        print("privacy check FAILED -- commit blocked:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("Remove the data, or (only if it is a false positive) commit with --no-verify after review.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
