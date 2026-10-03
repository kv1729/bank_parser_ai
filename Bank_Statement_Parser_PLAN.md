# Bank Statement Parser AI — PLAN.md

## 1. Mission

Build a reliable, fast bank-statement parser that accepts a bank statement PDF and produces validated structured transaction data.

The long-term system should be able to:

1. Detect whether a document matches a known bank/layout template.
2. Parse known layouts deterministically.
3. Validate extracted data using document/accounting invariants.
4. For unknown layouts, eventually use AI/agentic reasoning to infer and create a new versioned template.
5. Persist validated data.
6. Eventually expose the parser to an autonomous agent through MCP or another tool interface.

### Core principles

- Accuracy over cleverness.
- Deterministic parsing for known layouts.
- Validation is mandatory.
- Avoid unnecessary model calls, OCR, network calls, MCP hops, and external services.
- Optimize latency and cost after correctness is established.
- Never allow an LLM/external tool to bypass deterministic validation.
- Preserve provenance.
- Prefer the simplest architecture that meets measured requirements.

---

# 2. Non-Negotiable Scope Boundary — OCR Comes Last

The initial implementation is **text-based PDFs only**.

Do not implement OCR during the initial text-PDF work.

The first objective is to make extraction, normalization, validation, and template reuse work reliably on PDFs containing digitally extractable text.

### Initial architecture

```text
Text-based PDF
    ↓
PDF Ingest
    ↓
Layout / Positional Analysis
    ↓
Bank + Layout Detection
    ↓
Known Template?
   ↙          ↘
 YES           NO
 ↓             ↓
Deterministic  Future AI-assisted
Template       Template Discovery
 ↓             ↓
Extraction     Candidate Template
      ↘       ↙
       Validation
           ↓
     Validated Output
```

Only after the text-PDF pipeline is satisfactory and benchmarked:

```text
Scanned / Image PDF
        ↓
      OCR
        ↓
Same normalized extraction + validation pipeline
```

If a page has no extractable text, flag it for future OCR rather than silently skipping it.

**Do not add OCR simply because it is technically possible or available through a tool/plugin/MCP.**

---

# 3. Current Starting Point

Treat the current repository as a prototype/source of reusable ideas, not as a mature architecture.

The architecture review identified:

- PDF extraction using pdfminer/pdfplumber.
- Existing regex heuristics for several header fields.
- An Ollama/Qwen wrapper.
- No transaction extraction pipeline.
- No typed `Statement` / `Transaction` schema.
- No `Decimal`-based money handling.
- No balance validation.
- No meaningful automated test suite.
- No benchmark harness.
- No robust bank/layout detection.
- Cache and encoding/blank-page/context-limit risks.

Reuse existing code only after inspection and testing.

The current implementation should not constrain the future architecture.

---

# 4. Target Architecture

```text
                  ┌──────────────────────┐
PDF ─────────────→│ Ingest               │
                  │ hash / safety checks │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │ Layout Analysis      │
                  │ words + x/y + tables │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │ Bank/Layout Detector │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │ Template Registry    │
                  └──────┬────────┬──────┘
                         │        │
                     known      unknown
                         │        │
                         ↓        ↓
                 Deterministic   Future AI/
                   Extraction    Agent Analysis
                         │        │
                         └───┬────┘
                             ↓
                  ┌──────────────────────┐
                  │ Normalized Schema    │
                  └──────────┬───────────┘
                             ↓
                  ┌──────────────────────┐
                  │ Deterministic        │
                  │ Validation           │
                  └──────────┬───────────┘
                             ↓
                  PASS / WARN / FAIL
                             ↓
                    Output / Review
```

### Architectural principle

Separate the system into:

1. **Deterministic extraction**
2. **Deterministic verification**
3. **Intelligence for uncertainty**

AI should help where uncertainty exists, not replace deterministic work that can be done reliably.

This architecture is a starting hypothesis, not an immutable requirement. Claude may change it when experiments provide evidence for a materially better approach.

---

# 5. Controlled Exploration Philosophy

Claude Code is explicitly allowed to explore alternatives.

Do **not** assume that the tools, libraries, MCPs, skills, plugins, models, or architecture mentioned in this plan are automatically optimal.

Claude may investigate:

- Python PDF/text extraction libraries.
- Table/layout extraction approaches.
- MCPs.
- Skills.
- Plugins/external services.
- Context7 documentation.
- Debugger MCP.
- Local LLMs.
- Document extraction services.
- Benchmarking utilities.
- Existing repository utilities.
- Alternative architectural patterns.

The objective is **not to maximize exploration**.

The objective is to discover a solution that provides the best measured combination of:

- accuracy;
- latency;
- cost;
- reliability;
- privacy;
- maintainability;
- development speed.

---

# 6. Exploration Budget and Guardrails

This is a critical project constraint.

Claude has freedom to explore, but exploration must be **bounded**.

## For each significant technical decision

### Step 1 — Discover

Identify **up to 3 serious candidates**.

Examples:

- Top 3 PDF extraction libraries.
- Top 3 table extraction approaches.
- Top 3 relevant MCPs.
- Top 3 useful skills.
- Top 3 plugins/services.
- Top 3 model approaches.

Do not deeply investigate every candidate.

### Step 2 — Triage

For each candidate, quickly assess:

| Criterion | Question |
|---|---|
| Accuracy | Can it preserve transaction rows, columns and amounts? |
| Layout awareness | Does it preserve x/y positions/table structure? |
| Reliability | Is output deterministic/reproducible? |
| Latency | Can it support the target latency? |
| Cost | Local/free/paid/API cost? |
| Privacy | Does financial data leave the environment? |
| Integration | How difficult is integration? |
| Complexity | What infrastructure does it add? |
| Validation | Can output be independently verified? |
| Maintenance | Is it documented and viable? |

### Step 3 — Experiment

Select **at most 2 candidates** for hands-on testing unless there is strong evidence that a third experiment can materially change the decision.

Use the existing sample PDFs first.

Prefer:

```text
small experiment → measured result → decision
```

over:

```text
large implementation → discover it was the wrong approach
```

### Step 4 — Decide

Record:

- candidates;
- experiment;
- observed result;
- selected/rejected option;
- reason;
- remaining uncertainty.

---

# 7. Exploration Stop Conditions

Claude should stop exploring when:

- a candidate satisfies the current acceptance criteria;
- another candidate is unlikely to materially improve accuracy, latency, cost, privacy, reliability, or maintainability;
- the experiment has answered the original question;
- additional research is only curiosity-driven;
- the current solution can be implemented and benchmarked to resolve the uncertainty.

### Important

If Claude is deciding between:

> "Research more"

and

> "Build a small experiment and measure it"

prefer the **small experiment and measurement** when practical.

Do not exhaust context/tokens searching for a theoretically optimal solution when an empirically adequate solution can be tested.

---

# 8. Tool / MCP / Skill / Plugin Policy

Claude has permission to use tools beyond those explicitly mentioned in this plan.

Known available capabilities include:

- Context7.
- Debugger MCP.

Claude may discover and evaluate additional:

- MCP servers;
- Claude skills;
- plugins;
- Python packages;
- document extraction services;
- local models;
- other developer tooling.

## Tool adoption rule

A tool should be adopted only if it materially improves one or more of:

- accuracy;
- latency;
- cost;
- reliability;
- development speed;
- debugging;
- maintainability.

Convenience alone is not sufficient.

## MCP policy

MCP is **not automatically part of the core parsing pipeline**.

MCP may be useful for:

1. development/diagnostics;
2. external document processing;
3. agent orchestration;
4. exposing the finished parser to an agent.

However, avoid adding MCP hops when a local function can perform the same work more directly.

Do not send entire PDFs or huge transaction datasets through model context unnecessarily.

## Plugin / external-service policy

Claude may evaluate external services if they appear materially useful.

Before real financial documents are sent externally:

- verify privacy/data handling;
- verify applicable terms/consent requirements;
- prefer synthetic or explicitly consented documents for experiments;
- estimate cost;
- compare against the local deterministic baseline.

No paid service becomes a dependency merely because it is easier to call.

---

# 9. Phase 0 — Groundwork

## Objective

Define the output contract and validation foundation before improving PDF extraction.

### Tasks

1. Define typed `Statement`.
2. Define typed `Transaction`.
3. Use `Decimal` for monetary values.
4. Define a validation-report structure.
5. Hand-label the existing SBI sample.
6. Add pytest.
7. Implement `validate_balance_chain(statement)`.
8. Test:
   - correct statement passes;
   - changing one amount fails;
   - malformed values fail safely.
9. Replace fixed cache filenames with SHA256 content-based document IDs.
10. Decide whether sample data contains real PII/financial data and document the decision.

### Do not start with

- LLM extraction;
- OCR;
- MCP integration;
- production DB integration;
- autonomous agent.

The first goal is a reliable output contract and validation layer.

---

# 10. Phase 1 — Deterministic Text-PDF MVP

## Objective

Reliably parse known text-based layouts for SBI and HDFC.

```text
PDF
 ↓
Ingest
 ↓
Text + word positions
 ↓
Layout analysis
 ↓
Bank/layout detection
 ↓
Template selection
 ↓
Deterministic extraction
 ↓
Normalization
 ↓
Validation
 ↓
Structured output
```

## Ingest

Implement:

- SHA256 file-content ID;
- document ID;
- page count;
- encrypted/password-protected detection;
- no-text page detection;
- file/page size limits;
- safe error handling.

## Layout

Preserve where available:

- words;
- x/y coordinates;
- page number;
- useful font/position information;
- table structure.

Do not reduce the PDF to one long string if doing so destroys layout information.

## Bank/Layout Detection

Use multiple signals where possible:

- bank/header identity;
- statement labels;
- transaction-table headers;
- column arrangement;
- stable layout markers.

Avoid loose matching such as matching `SBI` inside `SBIN` or `SBI CARDS`.

## Templates

Each template should define:

- bank;
- layout identifier;
- template version;
- header extraction rules;
- transaction column boundaries;
- date parsing;
- amount parsing;
- debit/credit interpretation;
- multiline description handling;
- page continuation behavior;
- validation expectations.

Templates must be versioned.

Never silently overwrite an existing template version.

## Initial investigation

- SBI: evaluate pdfplumber table extraction because the sample exposes clean rows.
- HDFC: evaluate word-position/column-boundary extraction because the sample does not provide ruled table lines.

These are hypotheses to test, not mandatory final implementations.

---

# 11. Validation Layer

Validation is a first-class subsystem.

Where a running balance exists:

```text
previous balance
+ credit
- debit
= current balance
```

Also validate applicable invariants:

- opening/closing balance reconciliation;
- page carry-forward;
- transaction totals;
- exactly one debit/credit where the format requires it;
- date ordering;
- IFSC format;
- page continuity;
- expected transaction-row structure.

## Capability-based validation

Not every statement provides every invariant.

Therefore:

> Validate every invariant that the document actually provides.

Do not reject a valid statement simply because it does not contain a running balance.

Return structured results such as:

- `PASS`
- `WARN`
- `FAIL`

with reasons and affected rows/pages.

Validation failure should lead to human review or, later, a bounded AI-assisted retry.

---

# 12. Phase 2 — Benchmark Harness

Do not optimize the architecture using only two synthetic samples.

Target benchmark:

- at least 20 statements;
- approximately 5 banks;
- multiple layouts where possible;
- labelled correct output.

Measure:

- fully correct statement percentage;
- transaction row precision/recall;
- field accuracy;
- header exact match;
- balance-check pass rate;
- p50 latency;
- p95 latency;
- cost per document;
- run-to-run consistency.

Compare:

1. deterministic templates;
2. LLM-only extraction;
3. hybrid extraction;
4. selected third-party/document extraction service where legally and operationally appropriate.

The benchmark, not preference, should determine whether AI/external extraction belongs in the production path.

---

# 13. Phase 3 — AI-Assisted Unknown Layouts

Only after the deterministic pipeline and benchmark are credible.

## Goal

Handle unfamiliar text-based statement layouts.

```text
New PDF
 ↓
Known template?
 ├── YES → deterministic parser
 │
 └── NO
      ↓
  AI-assisted layout analysis
      ↓
  Candidate template
      ↓
  Deterministic extraction
      ↓
  Deterministic validation
      ↓
  PASS → version/save template
  FAIL → bounded retry / human review
```

Prefer AI to infer the **layout/template**, not manually transcribe every numeric row.

If structured LLM output is used:

- use schema-constrained output;
- minimize context;
- never trust generated numbers without validation;
- record model/configuration;
- test repeatability;
- benchmark latency/cost.

Unknown-layout templates must be versioned and never silently replace previous templates.

---

# 14. Phase 4 — OCR for Scanned PDFs

OCR begins **only after text-based PDFs are satisfactory and benchmarked**.

Scope:

- detect scanned/image-only pages;
- OCR route;
- normalize OCR output into the same internal representation;
- run the same validation layer;
- benchmark OCR accuracy and latency;
- handle noisy text;
- handle rotated pages;
- handle watermarks;
- handle mixed text/image PDFs.

OCR is an additional ingestion/extraction capability, not a replacement for the deterministic text-PDF path.

---

# 15. Phase 5 — Persistence and Service Layer

After extraction/validation is reliable:

- database persistence;
- document deduplication;
- stable transaction IDs;
- idempotent processing;
- CLI/API;
- structured error handling;
- audit/provenance records;
- safe logging;
- operational metrics.

Potential transaction identity can combine:

- document/account identity;
- date;
- amount;
- running balance;
- row position;

with care around legitimate duplicate transactions.

---

# 16. Phase 6 — MCP / Autonomous Agent Interface

Only introduce MCP as a core interface if agent orchestration materially benefits from it.

Potential tool:

```text
parse_statement(document_id)
```

The MCP layer should expose concise structured results rather than pushing entire PDFs or full transaction datasets into model context.

Possible agent responsibilities:

1. inspect incoming document;
2. identify known/unknown layout;
3. select an extraction strategy;
4. request creation of a new template;
5. run validation;
6. retry within bounded limits;
7. save/version approved templates;
8. request human review when validation/confidence is insufficient.

The parser must remain independently testable without the agent.

---

# 17. Agent Autonomy Guardrails

## Claude is allowed to

- inspect the entire repository;
- inspect documentation;
- use Context7;
- use Debugger MCP;
- discover MCPs;
- discover skills;
- discover plugins;
- evaluate Python libraries;
- evaluate local models;
- evaluate external services;
- create small experiments;
- run tests;
- compare extraction strategies;
- challenge assumptions;
- propose architecture changes;
- modify implementation after evidence-based decisions.

## Claude is bounded by

For each significant decision:

- discover ≤3 serious candidates;
- experimentally test ≤2 unless justified;
- start with existing samples;
- avoid repeated experiments answering the same question;
- stop when acceptance criteria are met;
- record meaningful decisions.

## Human approval is required before

- sending real financial documents to a third-party service;
- changing production database schemas/data;
- destructive data operations;
- adding expensive recurring infrastructure;
- committing to a paid API;
- deleting/replacing historical templates;
- materially expanding project scope.

---

# 18. Security / Privacy / Reliability

Treat bank statements as sensitive financial data.

Requirements:

- do not print full statements unnecessarily;
- do not log names, addresses, account numbers, PAN, email, or transaction descriptions unless required;
- redact sensitive debugging output;
- prefer local processing;
- use least-privilege DB credentials;
- make processing idempotent;
- avoid fixed shared cache paths;
- use document-specific cache keys;
- impose PDF/page/size/time limits;
- handle malformed/hostile PDFs safely;
- never allow transaction descriptions to become LLM instructions;
- keep validation outside the LLM;
- preserve audit/provenance without exposing sensitive content.

If sample data is real financial/PII data, explicitly decide whether it should remain in repository history and whether history rewriting is required.

---

# 19. Initial MVP — Definition of Done

The initial MVP is **not** “an AI agent that parses every bank statement.”

It is:

### Input

A text-based SBI or HDFC statement PDF.

### Output

A typed `Statement` containing:

- statement metadata;
- account/header information;
- transactions;
- normalized monetary values;
- validation report;
- template/version/provenance metadata.

### Acceptance criteria

1. No OCR required.
2. No LLM required for known layouts.
3. Correct transaction extraction for the labelled benchmark.
4. Monetary values use `Decimal`.
5. Validation catches intentional amount corruption.
6. Cache/document identity is content-based.
7. No cross-document cache contamination.
8. Multiline descriptions are handled where present.
9. Automated tests cover parser behavior.
10. Failures are explicit rather than silently skipped.
11. Parser can report that a document requires future OCR.
12. Latency is measured rather than assumed.

---

# 20. Future Success Criteria

Eventually target:

- very high accuracy on supported layouts;
- predictable validation;
- low p50/p95 latency;
- low cost per document;
- safe handling of sensitive data;
- automatic template reuse;
- versioned templates;
- bounded autonomous reasoning;
- human review for unresolved cases;
- reproducible results;
- measurable improvement as benchmark coverage expands.

“100% accuracy” may be an aspirational product goal, but the engineering process must measure actual benchmark accuracy and route uncertain cases to review rather than claim correctness.

---

# 21. First Claude Code Task

Start with the smallest useful implementation.

## Task

1. Inspect the repository.
2. Do not redesign the entire system yet.
3. Define `Statement` and `Transaction`.
4. Use `Decimal`.
5. Create labelled expected JSON for the existing SBI sample.
6. Implement `validate_balance_chain(statement)`.
7. Add pytest:
   - correct SBI ground truth passes;
   - one changed amount fails;
   - malformed values fail safely.
8. Run tests.
9. Report exactly what changed and what remains.

## Do not do yet

- OCR;
- MCP integration;
- production database integration;
- autonomous agent;
- broad refactoring;
- external paid-service integration;
- large-scale benchmark collection.

If Claude discovers a potentially useful tool/library/MCP during this task, record it as a candidate in `DECISIONS.md` rather than expanding scope automatically.

---

# 22. Decision Log

Maintain a lightweight `DECISIONS.md`.

For meaningful architectural decisions record:

```text
Decision:
Date:
Question:
Candidates considered:
Experiment:
Observed evidence:
Decision:
Reason:
Rejected alternatives:
Follow-up:
```

This prevents useful exploration from disappearing while avoiding unnecessary documentation overhead.

---

# 23. Recommended Claude Code Operating Instruction

The following instruction should be given to Claude Code when starting work on this repository:

> You have architectural freedom.
>
> Do not assume the tools, libraries, MCPs, skills, plugins, models, or architecture described in PLAN.md are optimal.
>
> Challenge assumptions when evidence suggests a better approach.
>
> You may use Context7, Debugger MCP, other MCPs, skills, plugins, Python libraries, local models, or external services when they provide measurable value.
>
> Optimize for **decision quality, not exploration volume**.
>
> For any significant technical decision:
>
> 1. Discover up to 3 credible alternatives.
> 2. Compare them on accuracy, latency, cost, privacy, reliability, integration complexity, and maintainability.
> 3. Experiment with no more than 2 unless there is strong evidence that a third experiment could materially change the decision.
> 4. Prefer small experiments using the existing sample documents.
> 5. Stop when the experiment provides sufficient evidence to make the decision.
> 6. Record the decision and evidence in DECISIONS.md.
>
> Do not maximize tool usage.
>
> Minimize unnecessary complexity, latency, token consumption, and cost.
>
> If uncertain between researching more and building a small experiment, prefer the small experiment and measurement when practical.
>
> You may change the proposed architecture if experimentation demonstrates that another architecture is materially better.
>
> Never send real financial documents to an external service without explicit approval.
>
> OCR is explicitly out of scope until the text-based PDF pipeline has reached its acceptance criteria and has been benchmarked.
>
> Do not turn every problem into an LLM problem.
>
> Prefer deterministic extraction and deterministic validation when they are demonstrably reliable.
>
> The final system should be judged by measured accuracy, validation success, latency, cost, privacy, and maintainability — not by how many AI tools it uses.

---

# 24. Working Philosophy

Follow:

```text
Explore
   ↓
Experiment
   ↓
Measure
   ↓
Decide
   ↓
Implement
   ↓
Validate
   ↓
Document
```

Not:

```text
Explore
   ↓
Explore more
   ↓
Add tools
   ↓
Add MCP
   ↓
Add AI
   ↓
Add complexity
```

Claude is encouraged to challenge assumptions.

Claude is **not** encouraged to maximize tool usage.

The objective is not to build the most sophisticated parser.

The objective is to build the **most reliable, fast, cost-efficient, maintainable parser that the evidence supports**.

---

# 25. Status (updated 2026-10-03)

Details and evidence for each item are in `DECISIONS.md`.

## Phase 0 — Groundwork

| # | Task | Status |
|---|---|---|
| 1–3 | Typed `Statement` / `Transaction`, `Decimal` money | Done — `bank_parser/schema.py` (D-001) |
| 4 | Validation-report structure | Done — `PASS` / `WARN` / `FAIL` / `SKIP` with codes, row, page (D-003) |
| 5 | Hand-label SBI sample | Done — 32 rows, local-only fixture (D-002, D-006) |
| 6–8 | pytest; `validate_balance_chain`; pass / corrupted / malformed tests | Done — 69 tests |
| 9 | SHA256 content-based document IDs / cache keys | **Not started** |
| 10 | Sample-data PII decision | **Open** (D-004) |

## Early Phase 1 evidence

- A real 9-page, password-protected SBI statement (local only, gitignored) was parsed with pdfplumber tables: 81 transactions, balance chain PASS on every row, and Dr/Cr counts, totals and closing balance all match the bank's printed summary. ~1.4 s per statement, almost all in table finding (D-005).
- pdfplumber silently drops a row split across pages in the synthetic SBI sample (D-002): validation, not the extractor, is what catches extraction errors.
- The synthetic and real SBI statements are different layouts; the real one draws column headings as vector shapes, so layout detection must use geometry and labels, not header text.

## Next

1. Content-based document ID / cache key (Phase 0 #9).
2. Summary-page validators: Dr/Cr counts, total debits/credits, closing balance.
3. Latency experiment: word positions + fixed column boundaries vs full table finding; adopt only if output is identical.
4. First versioned SBI templates (synthetic layout and current layout) behind a layout detector.
5. HDFC sample ground truth.

## Status update — product architecture (2026-10-03)

Implemented per `INSTRUCTIONS.md`; design in `docs/ARCHITECTURE.md`, evidence in `DECISIONS.md` D-007 – D-013.

| Area | Status |
|---|---|
| Phase 0 #9 content-based document IDs | Done — sha256 document IDs, content-addressed store (D-012) |
| Phase 1 deterministic text-PDF MVP | Done for 3 layouts (synthetic SBI, synthetic HDFC, real SBI) — `word_columns` engine (D-010) |
| Templates, versioning, registry | Done — append-only, exclusive-create (D-009) |
| Validation layer (§11) | Balance chain, reconciliation vs printed totals, rows, header; capability-based `SKIP` |
| Unknown layouts without waiting (Workflow 1) | Done — inferred layout, verified by arithmetic |
| Template agent (Workflow 2, Phase 3) | Done with a deterministic proposer; LLM proposer slot disabled (D-011) |
| Persistence / service / app (Phase 5) | SQLite, FastAPI, upload UI; no auth yet (D-013) |
| Benchmark harness (Phase 2) | Latency harness done (`experiments/benchmark.py`); accuracy corpus still 3 documents |
| OCR (Phase 4), MCP interface (Phase 6) | Not started |

**Next:**
1. Decide on PyMuPDF licensing (D-007).
2. Grow the labelled corpus toward PLAN §12 (≥20 statements, ~5 banks).
3. Handle layouts without ruled cells and without header text.
4. Merge classification into the extraction pass.
5. Make background jobs survive restarts.
6. Add authentication before any non-local deployment.
