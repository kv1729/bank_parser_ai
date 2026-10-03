You are working on the bank_parser_ai repository.

We are now moving from the current parser prototype/Phase 0 work toward the actual product architecture.

Before making implementation changes, read and understand:

1. CLAUDE.md
2. PLAN.md
3. DECISIONS.md
4. README.md
5. docs/ARCHITECTURE_REVIEW.md
6. the complete repository structure
7. the existing parser implementation
8. existing tests and fixtures
9. pyproject.toml / uv configuration
10. Docker configuration if present
11. database configuration if present

Also inspect the git history where useful to understand why important decisions were made.

Do not assume the existing architecture is correct.

The repository should become the implementation of the architecture described below.

==================================================
1. PRODUCT GOAL
==================================================

We are building a bank statement processing system.

A user uploads a bank statement PDF through an application.

For now:

- focus ONLY on text-based PDFs;
- OCR/image-based PDF processing is a later phase;
- do not implement OCR as part of this task unless needed only for document classification.

The application should allow a user to upload a bank statement and eventually see the extracted structured information.

There should be NO artificial application-level document-size cap.

Do not implement arbitrary limits such as "maximum 10 MB" simply for convenience.

However, the system must still be engineered safely for large documents using streaming/chunked handling, temporary storage, resource-aware processing, and configurable infrastructure/resource protection where necessary.

The distinction is:

NO PRODUCT SIZE CAP

does not mean:

UNBOUNDED RESOURCE CONSUMPTION.

==================================================
2. REQUIRED OUTPUT
==================================================

For every successfully processed statement, the system should extract at minimum:

### Account / statement information

- account holder
- account number
- account-holder address, if present
- bank name
- bank address, if present
- statement start date
- statement end date

### Transactions

Every transaction should preserve all useful information available in the source document.

At minimum:

- transaction date
- transaction description / summary
- transaction amount
- debit or credit classification
- running balance, if present
- transaction/reference identifier, if present
- page/source information where useful for provenance

Do not throw away useful transaction fields simply because the current MVP does not need them.

The schema should be extensible.

Amounts must use Decimal or an equivalent exact monetary representation.

Do NOT use float for financial values.

==================================================
3. MOST IMPORTANT ARCHITECTURAL REQUIREMENT
==================================================

The system must NOT block extraction while a new template is being created.

This is the central architectural requirement.

Suppose a PDF arrives.

Case A:

A matching bank/layout template exists.

Then:

PDF
→ identify template
→ extract using the known extraction strategy
→ validate
→ store result

Case B:

No matching template exists.

Then TWO workflows should be initiated.

WORKFLOW 1 — EXTRACTION

Immediately attempt to extract the information from the current PDF.

The user should not have to wait for the template-learning process.

WORKFLOW 2 — TEMPLATE CREATION

In parallel, a template-learning agent/process should:

1. analyze the document;
2. understand its structure;
3. identify bank/layout characteristics;
4. determine appropriate extraction rules;
5. create a candidate template;
6. run regression tests;
7. validate the candidate template against the source document;
8. version the template;
9. save it to the template registry only when acceptance criteria are satisfied.

The template-learning workflow may take substantially longer.

That is acceptable.

The extraction workflow must not wait for it.

==================================================
4. VERY IMPORTANT: DO NOT TURN EVERYTHING INTO AN AGENT
==================================================

We want agentic behavior where reasoning is actually required.

Do NOT create an LLM agent for every operation.

For example:

PDF ingestion should probably be deterministic.

File hashing should be deterministic.

PDF type detection should probably be deterministic.

Known-template selection should preferably be deterministic.

Known-template extraction should preferably be deterministic.

Validation should be deterministic.

Database persistence should be deterministic.

The agent becomes valuable primarily when:

- the bank/layout is unknown;
- a new template must be inferred;
- extraction strategy is uncertain;
- competing extraction methods need to be evaluated;
- a template needs to be generated and tested;
- a failed extraction needs bounded reasoning/recovery.

The architecture should explicitly distinguish:

DETERMINISTIC WORK

from

AGENTIC WORK.

==================================================
5. EXTRACTION STRATEGY TOOLBOX
==================================================

For extraction, we have three broad classes of tools available:

1. Python libraries/functions
2. Claude skills
3. MCPs

LLM/agent reasoning may also be used when necessary.

Do NOT assume that MCP is automatically better than Python.

Do NOT assume that a skill is automatically better than Python.

Do NOT assume that an external service is automatically better than local extraction.

The system should choose the simplest reliable method.

For example:

KNOWN TEMPLATE:

PDF
→ deterministic Python extraction
→ validation

UNKNOWN TEMPLATE:

PDF
→ agent evaluates available extraction capabilities
→ chooses an appropriate approach
→ extraction
→ validation

The agent may consider:

- existing Python libraries;
- existing project skills;
- available MCPs;
- plugins/external services;
- local models;
- other documented capabilities.

==================================================
6. MCP / SKILLS / PLUGINS MUST BE EVALUATED
==================================================

We specifically want this project to explore MCPs, skills and plugins.

However, we do NOT want pointless tool usage.

For every significant extraction/tooling decision:

1. discover up to 3 credible candidates;
2. evaluate them;
3. experimentally test at most 2 initially;
4. compare measurable results;
5. select the simplest option that meets requirements.

Evaluate at minimum:

- accuracy;
- transaction extraction quality;
- layout awareness;
- latency;
- cost;
- privacy;
- reliability;
- reproducibility;
- integration complexity;
- maintenance burden.

Context7 is available and should be used when library/API documentation needs verification.

Debugger MCP is available and may be used when useful.

You may discover additional MCPs, skills and plugins.

Do not artificially restrict yourself to the tools we already know.

But do not add tools simply because they exist.

==================================================
7. TOOL COST MODEL
==================================================

For every MCP/plugin/external model/service considered, determine:

- Is it local?
- Does it require an API?
- Does it consume tokens?
- Does it have per-document cost?
- Does it send financial data outside the local environment?
- Does it add latency?
- Does it require another service/process?
- Does it create vendor lock-in?
- Does it materially improve extraction quality?

Record this in DECISIONS.md.

The system should prefer:

LOCAL + DETERMINISTIC + VALIDATABLE

when that approach meets requirements.

External services are allowed to be evaluated, but real financial documents must not be sent externally without explicit approval.

Use synthetic or explicitly consented documents for external-service experiments.

==================================================
8. TEMPLATE SYSTEM
==================================================

Templates are a first-class concept.

A template must not simply mean "regexes for Bank X".

A template should describe the extraction contract for a particular bank/layout/version.

It may contain:

- bank identity;
- layout identity;
- template version;
- identifying markers;
- header extraction rules;
- transaction column boundaries;
- date format;
- amount format;
- debit/credit interpretation;
- multiline-description rules;
- page continuation rules;
- validation expectations;
- extraction strategy;
- provenance;
- creation timestamp;
- regression-test status.

Templates must be versioned.

Never silently overwrite an existing template.

If a bank changes its statement layout:

Bank X
  template v1
  template v2
  template v3

not:

Bank X
  one template that keeps changing.

==================================================
9. TEMPLATE AGENT
==================================================

Design a dedicated template-learning workflow.

Its responsibility is NOT to parse every future document.

Its responsibility is to create a reusable deterministic capability.

For an unknown bank/layout:

1. analyze the PDF;
2. understand its layout;
3. identify stable markers;
4. identify header fields;
5. identify transaction columns;
6. understand debit/credit representation;
7. understand multiline rows;
8. identify balance behavior;
9. create candidate extraction rules;
10. create a template;
11. run the parser using that template;
12. run regression/validation tests;
13. compare extracted output against expected/source invariants;
14. reject the template if validation fails;
15. optionally perform a bounded retry/refinement;
16. save/version the template only after passing acceptance criteria.

The agent should not be allowed to endlessly retry.

Define bounded retry/experiment limits.

For example:

- maximum candidate strategies;
- maximum refinement attempts;
- maximum token budget if applicable;
- maximum wall-clock time;
- clear failure state.

If it cannot produce a validated template, mark the document/template as requiring human review.

==================================================
10. EXTRACTION WORKFLOW
==================================================

Extraction should be independent of template creation.

A conceptual flow:

UPLOAD
 ↓
Document ID / storage
 ↓
PDF classification
 ↓
Text-based?
 ↓
Extract structural information
 ↓
Bank/layout detection
 ↓
Template lookup
 ↓
 ┌───────────────────────────────┐
 │                               │
Known template              Unknown template
 │                               │
 ↓                               ↓
Known extraction              Extraction strategy
strategy selection             selection
 │                               │
 └───────────────┬───────────────┘
                 ↓
             Extraction
                 ↓
             Normalize
                 ↓
             Validate
                 ↓
             Persist
                 ↓
          Display in app