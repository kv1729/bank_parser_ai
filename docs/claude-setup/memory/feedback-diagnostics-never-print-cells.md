---
name: feedback-diagnostics-never-print-cells
description: "Statement diagnostics may print only counts, positions and fixed heading-vocabulary words — never raw cell or row text, even \"header\" rows"
metadata:
  node_type: memory
  type: feedback
  modified: 2026-10-14T04:24:55.647Z
---

When diagnosing a real (private) bank statement, print only structure: counts, x/y positions, ratios, chosen formats, and words that belong to a fixed column-heading vocabulary (date, narration, remarks, debit, credit, balance, …). Never print a cell's or row's text, even when it is assumed to be the header row.

**Why:** On 2026-10-14 a layout diagnostic printed the "labels" of the first ruled row on each page. On page 2 that row was a transaction, so a real date, reference, counterparty and amounts leaked into the conversation. This breaks the owner's global rule 3 (never view protected data).

**How to apply:** Filter every printed word through the heading vocabulary or an allow-list mask, and replace other text with shape masks (letters→a, digits→9). Assume any row could be data. If exposure happens anyway, tell the owner immediately and don't reuse it. See [[project-bank-parser-privacy]].
