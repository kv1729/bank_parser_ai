# Test fixtures (local only)

Expected-output files (`*.expected.json`) contain statement data — names, account
numbers, transaction descriptions — so they are **gitignored and never committed**.
Tests that need a missing fixture are skipped, not failed.

| File | Source PDF | How it was made |
| --- | --- | --- |
| `sbi_sample.expected.json` | `sample_data/SBI_Bank_Statement_Chenna_Reddy.pdf` | Drafted from pdfplumber tables, then hand-verified against the PDF text and the balance chain. The 30 Jul 2018 row (split across pages 1–2) was added by hand because pdfplumber drops it. See DECISIONS.md D-002. |

Format: `schema_version`, `header`, `transactions`. Money is a plain decimal string
(`"120749.89"`), dates are ISO (`"2018-05-03"`), missing values are `null`.

Expected output for real statements must live outside the repo, or in a gitignored path.
