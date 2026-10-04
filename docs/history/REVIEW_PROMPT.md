> **Superseded.** This was the prompt for the initial architecture review (result: `docs/ARCHITECTURE_REVIEW.md`). The current plan is `Bank_Statement_Parser_PLAN.md`.

Read PLAN.md carefully.

This repository contains an existing bank statement parser that I built previously.

I want to restart and evolve it into the architecture described in PLAN.md.

For this task, DO NOT modify any files.

First inspect the entire repository and understand the existing implementation.

I want you to determine:

1. What the current architecture is.
2. How PDFs are currently parsed.
3. How text extraction works.
4. How the current LLM integration works.
5. What extraction schema currently exists.
6. What parsing logic can be reused.
7. What tests currently exist.
8. What parts of PLAN.md are already implemented.
9. What parts are missing.
10. What assumptions in PLAN.md are technically questionable.
11. What the minimum viable architecture should be for Phase 1.
12. Where MCP would actually add value and where it would unnecessarily add latency or complexity.
13. How you would benchmark:
    - deterministic template extraction
    - DocuClipper
    - LLM-assisted extraction
    - hybrid approaches
14. Identify any security, reliability, idempotency, or financial-data risks.

Do not implement anything yet.

At the end, produce:

A. Current architecture
B. Gap analysis
C. Recommended architecture
D. Risks/assumptions to validate
E. Proposed implementation phases
F. The single smallest first implementation task

Challenge my assumptions where appropriate. Do not simply agree with PLAN.md.