---
name: metareview
description: Two scripted workflows for one agent: a systematic review (nine steps, narrative synthesis) and a meta-analysis (eight steps, from a question or a coding sheet). The engine says what to do at every step.
---

# metareview: the script

You are the one agent that does the whole job. You run the engine's commands and you do every reading task
yourself (screening, extraction, risk of bias, critique), one task at a time, following the role instructions
in `agents/<role>.md`. The engine validates every file you write and computes every number. No subagents.

Define once:

```
MR = python3 "${CLAUDE_PLUGIN_ROOT}/mr.py"
```

(Outside a plugin, `${CLAUDE_PLUGIN_ROOT}` is the folder holding this file.) Projects live in
`./reviews/<slug>/` under the current working directory (override with `MR_HOME`).

## The loop (both workflows)

1. Start the project (see the workflow sections). Record your model: `MR settings ai_model "<your model name>"`.
2. Run `MR next`. Do exactly what the `NEXT:` line says. It is always one of:
   - **write protocol.json** (see below), then `MR protocol check`;
   - **run a command** (`MR search`, `MR screen ta`, `MR fulltext`, `MR screen ft`, `MR extract`, `MR rob`, `MR analyze`, `MR report`);
   - **do a task YOURSELF**: for each listed line, read the named `agents/<role>.md` once (screener, extractor,
     appraiser, critic), open the input files on that line, perform the task exactly as the role file says, and
     write the output file at the listed path. Work through the lines one by one without pausing.
3. Repeat until `NEXT:` says `DONE`. Tell the researcher where `report/index.html` is and that it is an
   unverified AI draft.

Do not ask the researcher questions unless the engine says the review cannot proceed (for example no full
texts at all). Make reasonable protocol choices yourself and record them in protocol.json.

## Systematic review (`/metareview:systematic-review`)

Start: `MR init "<question>" --mode sr --reviewers single` (add `--with-meta-analysis` if pooling was asked for).

The nine standard steps map onto the pipeline like this:
1. *Choose the review type*: a systematic review suits a focused, answerable question about existing primary
   studies. If the question is a broad topic or an open design question, narrow it and say so in the protocol.
2. *Formulate the question*: PICO in protocol.json.
3. *Team*: one AI agent plus the human researcher; the report says so. Dual blind review exists
   (`--reviewers dual`) when independent double screening is required.
4. *Protocol*: written before searching, locked by `protocol check`; any later change is logged as a deviation.
5. *Search*: the databases chosen for this question (PubMed, Europe PMC, OpenAlex, Semantic Scholar, ClinicalTrials.gov),
   every hit retrieved, duplicates merged across sources, each query and count recorded (PRISMA-S).
6. *Select studies*: criteria applied at title/abstract then full text; every exclusion carries a code and reason.
7. *Extract data*: the standardized form in `agents/extractor.md`; every number must sit in a verbatim quote.
8. *Synthesize*: a qualitative synthesis is always produced (direction of effect per study, SWiM vote count);
   quantitative pooling only when requested and when 2+ studies report comparable data.
9. *Disseminate*: `report/index.html` and `report.md`, PRISMA flow, study tables, risk of bias, limitations, AI use.

## Meta-analysis (`/metareview:meta-analysis`)

Two starting points:
- From a question: `MR init "<question>" --mode ma --reviewers single`, then the loop. Steps 1-2 and 6 of
  the eight-step guide are done by you (question, search and selection, coding = extraction); steps 3-5, 7-8 by
  the engine.
- From a coding sheet the researcher already prepared: `MR ma import <file.csv> --measure RR|OR|HR|MD|SMD|r --question "<...>"`.
  Columns: `study` plus one effect shape (`events_1,n_1,events_2,n_2` | `mean_1,sd_1,n_1,mean_2,sd_2,n_2` |
  `estimate,ci_low,ci_high` | `estimate,se` | `r,n`); optional `year`, `outcome`, `rob`; any other column
  becomes a moderator (text → subgroup, numeric → meta-regression). Then `MR analyze`, `MR report`, critique, done.

The eight steps (Hansen, Steinmetz & Block 2021) as the engine runs them:
1. Research question: constructs or intervention; note existing meta-analyses in the protocol.
2. Literature search and inclusion: systematic, reproducible, transparent; duplicates removed, abstract then
   full-text screening, PRISMA flow. Grey literature is not excluded by design.
3. Effect size: RR/OR/HR (log scale), MD/SMD (Hedges' g), Fisher-z correlations; one measure per outcome,
   never mixed; reported estimates with CI or SE accepted. Partial and bivariate correlations are not mixed.
4. Analytical method: univariate random-effects meta-analysis; subgroups and meta-regression for moderators.
5. Software: the built-in engine, verified against metafor to four decimals (tests/test_meta.py).
6. Coding: one row per study-outcome; moderators coded alongside; multiple effect sizes per study are not
   pooled together (choose the protocol timepoint); the sheet is exported as `report/coding_sheet.csv`.
7. Analysis: outlier/influence diagnostics (studentized residuals, Cook's distance) with a without-outliers
   re-run; publication bias triangulated (funnel plot, Egger, Begg rank, trim-and-fill, fail-safe N,
   cumulative by year); random-effects model (REML, Knapp-Hartung), fixed-effect shown for comparison;
   τ², I², prediction interval; moderators.
8. Reporting: pooled effect, SE/CI, k and n, heterogeneity, forest plot, all tests, draft GRADE, plus the
   coding sheet and `analysis/results.json` for open science.

## Writing protocol.json

`init` leaves a template with every key. Fill it before `protocol check`:

- `pico`: population, intervention_or_exposure, comparator, outcomes, each one sentence.
- `designs`: subset of `rct`, `cohort`, `case-control`, `cross-sectional`, `diagnostic`, `prognostic`. Set
  `exposure_study: true` for exposure/risk-factor questions (ROBINS-E is then used).
- `inclusion` / `exclusion`: numbered criteria; each exclusion has a `code` (POPULATION, INTERVENTION,
  COMPARATOR, OUTCOME, DESIGN, NOT_PRIMARY, LANGUAGE, OTHER).
- `outcomes`: one entry per outcome, exactly one `primary: true`. `type` binary (counts; RR or OR),
  continuous (means/SDs; MD or SMD), effect (reported HR/RR/OR/MD with CI; measure must match) or
  correlation (r, n). `lower_is_better` true/false. `mid`: minimal important difference on the measure's
  scale, or null to use the null threshold.
- `search.sources`: **choose the databases before writing any query.** Run `MR sources` to see the catalog and the
  suggestion for this question (also printed by `init`). Pick by where the evidence on this topic is published:
  - clinical, medical, public-health → `pubmed` + `europepmc` (+ `clinicaltrials` for interventions: unpublished trials);
  - economics, labour, income, policy, social science, environment, disasters → `openalex` + `semanticscholar`;
  - questions that cross both (e.g. health effects of job loss) → `pubmed` + `openalex` (+ a third if useful).
  At least 2 sources (or a written `single_source_reason`). Each entry is `{"source", "query", "rationale"}`; the
  `rationale` says in one sentence why this database fits the question. Write each query **in that database's own
  syntax** (shown by `MR sources`): 2-3 concepts (population, intervention/exposure, sometimes design), each as
  controlled vocabulary where the database has it (MeSH for PubMed) OR free-text synonyms, joined with AND. No outcome
  terms. No language limit unless the protocol says so.
  Every hit of every query is retrieved. PubMed result sets above 9,999 are split by publication date so the whole
  set comes back. `protocol check` prints the hit count per source and stops if the combined total is not
  screenable (default 20,000); then make the queries more specific, keeping all synonyms.
- `analysis`: `pool` (false in sr mode), REML + Knapp-Hartung by default; `subgroups` and `meta_regression`
  list extraction subgroup keys (for example `["design"]`, `["year"]`).

## Rules

- Never edit files under `reviews/` except `protocol.json`, the `settings` command, and the exact output
  paths the NEXT line names.
- Never write numbers into the report by hand. The engine computes and writes everything.
- A rejected file (the engine prints `REJECTED` or `INVALID` with reasons) is fixed by deleting it and
  redoing that task from the paper with the reasons in mind. Never invent a number or a quote.
- If a researcher drops PDFs into `reviews/<slug>/fulltext/uploads/` as `<pmid>.pdf` or `.txt`, run
  `MR fulltext` again; the engine picks them up.
- `MR settings signoff "<name>"` is for the human researcher only. Never run it yourself.
- API mode: with a configured key (`MR config`), `MR run "<question>"` performs the whole loop with direct
  model calls; use it only when asked for a fully automatic run outside this session.
