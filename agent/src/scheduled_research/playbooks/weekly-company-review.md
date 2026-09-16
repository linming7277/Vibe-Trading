---
name: Weekly Company Review
description: Week-in-review for a configured list of companies — price against value zones, announcements, headlines and data freshness, one section per company.
markets: [cn]
suggested_schedule: "0 9 * * 6"
suggested_timezone: Asia/Shanghai
data_capabilities:
  - Daily open, high, low, close and volume history for listed companies over recent months
  - Company announcements and news headlines published during the review window, with timestamps
  - The fair-value zone ranges and support zones maintained for each company by the research workspace
  - Fundamental and valuation snapshot fields for each company, with the report date they come from
variables:
  companies: "(no company list configured)"
---

# Weekly company review

Produce a week-in-review for each company in the list below, written for a
reader who follows these companies but did not watch the tape this week.

Resolve the current date and time from the run environment on every run. This
instruction text is stored once and replayed on every fire, so it contains no
date of its own — never assume the day it was written is the day it is running.

## Inputs

- Companies: {{companies}}

If the company list above is empty or says none is configured, reply with a
single line saying so and produce no other content rather than inventing
subjects to review.

## Data to gather

For each company, covering the window from the prior week's final close to the
most recent completed session:

1. The last close and the week's change, compared against the company's own
   recent daily range so noise stays labelled as noise.
2. Where the last close sits relative to the fair-value zone ranges kept for
   the company, quoting the ranges themselves and their as-of date.
3. Company announcements published in the window, each with its publication
   date. Describe only what the text says; do not infer materiality.
4. News headlines naming the company in the window, each with its timestamp.
5. The freshest fundamental and valuation snapshot fields available, with the
   report date they were filed under, so an old filing stays visibly old.

## Method

- One self-contained section per company; a reader should not need to cross
  between sections to understand any single company.
- Separate what moved from why it moved. Only assert a cause when a retrieved
  source states that cause; otherwise report the move and the coincident
  headline as two separate facts.
- Order companies by how much new, dated information appeared, not by how
  dramatic the price move was.

## When data is missing

A source that returns nothing, errors out, or is not configured is a fact to
report, not a gap to fill.

- Name every missing item explicitly in a `Data gaps` section, with the reason
  when the failure gave one.
- Continue using only the evidence actually retrieved.
- Never substitute a value from memory, from a general prior, from a
  third-party summary, or from an earlier run of this playbook. A number that
  did not come back from a source on this run does not appear in this report.
- Never present a stale figure as current. If the freshest value available is
  older than the window this review covers, print its as-of date beside it.
- If a whole company section has no evidence, keep its heading and write
  `no data retrieved` under it.
- Every figure carries its as-of date and the source it came from.

## Output

Markdown, in this order:

1. `## Week in one paragraph` — the window covered, the companies reviewed,
   and the handful of dated items that mattered.
2. `## Companies` — one subsection per company: price against value zones with
   the ranges quoted, announcements, headlines, and the data-freshness note.
3. `## Data gaps` — always present; write `none` when nothing was missing.

Keep the whole review under roughly 900 words.

## Boundaries

- This is a factual review. No buy, sell, or hold calls, no price targets,
  no position sizing, no leverage suggestions.
- Do not place, modify, or cancel any order, and do not touch a live trading
  connector.
- Do not forecast next week's direction. Report the week; the reader decides.
