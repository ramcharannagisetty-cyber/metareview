# metareview

One agent, two scripted workflows:

- `/metareview:systematic-review <question>`: the nine standard steps (choose review type, question, team,
  protocol, search, selection, extraction, synthesis, report). Qualitative (SWiM) synthesis; pooling optional.
- `/metareview:meta-analysis <question | coding_sheet.csv>`: the eight steps of Hansen, Steinmetz & Block
  (2021): question, search, effect size, method, software, coding, analysis (outliers, triangulated
  publication bias, random effects, moderators), reporting. Works from a question or from your own coding sheet.
- `/metareview:review <question>`: both at once (systematic review with meta-analysis).

The agent reads papers and proposes judgements; code does every count, check and calculation.

```
question ─► protocol (PICO, criteria, outcomes, PubMed query; locked)
         ─► source choice (agent picks databases for the topic: PubMed, Europe PMC, OpenAlex,
            Semantic Scholar, ClinicalTrials.gov) and one query per database in its own syntax
         ─► search (every hit retrieved, PubMed >9,999 split by date; duplicates merged across sources)
         ─► title/abstract screening: 2 blind AI reviewers, kappa, any-include advances
         ─► full texts (Europe PMC open access; your PDFs in fulltext/uploads/)
         ─► full-text screening: 2 blind AI reviewers + AI adjudicator
         ─► extraction: 2 blind AI extractors; every number must sit in a verbatim quote found in the paper;
            disagreements reconciled field by field
         ─► risk of bias: RoB 2 / ROBINS-I / ROBINS-E / QUIPS / QUADAS-2 by design; overall by worst domain
         ─► analysis: random effects (REML, Knapp-Hartung), τ², I², prediction interval,
            leave-one-out, DL sensitivity, Egger, subgroups, draft Core-GRADE certainty
         ─► report: Markdown + HTML, PRISMA flow, forest and funnel plots, summary of findings
         ─► independent AI critic; findings appended
```

The statistics engine is pure Python and reproduces metafor (`rma.uni`) on the BCG vaccine dataset
to four decimals (tests/test_meta.py).

## Meta-analysis from your own coding sheet

```bash
python3 mr.py ma import studies.csv --measure RR --question "Does X reduce Y?"
python3 mr.py analyze && python3 mr.py report
```

`studies.csv` has a `study` column plus one effect shape: `events_1,n_1,events_2,n_2` | `mean_1,sd_1,n_1,mean_2,sd_2,n_2` |
`estimate,ci_low,ci_high` | `estimate,se` | `r,n`. Optional `year`, `outcome`, `rob`. Every other column is a
moderator: text columns become subgroups, numeric columns meta-regression variables. Output: pooled effect with
τ², I² and prediction interval; fixed-effect comparison; leave-one-out; outlier/influence diagnostics; Egger,
Begg, trim-and-fill, fail-safe N, cumulative meta-analysis; subgroups and meta-regression; draft GRADE;
forest and funnel plots; `report/coding_sheet.csv` and `analysis/results.json` for open science.

## Run with your own API key (no Claude Code needed)

```bash
pip install anthropic            # or: pip install openai
python3 mr.py config --provider anthropic --anthropic-key sk-ant-...   # stored in ~/.config/metareview/config.json (mode 600)
python3 mr.py run "Does colchicine reduce major cardiovascular events in coronary artery disease?"
```

`run` drives every stage itself: it drafts and locks the protocol, searches PubMed, calls the model once per
reviewer/extractor/appraiser task (A and B are separate requests and never see each other), validates every
answer, retries rejected ones with the engine's reasons, pools the results and writes the report. Rerun
`python3 mr.py run` to continue after an interruption; `--max-steps` caps model calls (default 400).

Model choice: `--provider anthropic|openai`, `--model <id>` (default `claude-opus-5-5` / `gpt-5`), `--effort low|medium|high`.
Keys may also come from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. Keys are never printed or written into reviews.

## Install (Claude Code)

```bash
claude plugin marketplace add /path/to/metareview
claude plugin install metareview@metareview-local
```

Then in any folder: `/metareview:review Does colchicine reduce major cardiovascular events in coronary disease?`

Optional: `export NCBI_EMAIL=you@example.org` (polite E-utilities use) and `pip install pypdf` (read PDFs).

## Use as a scripted pipeline with another agent

`python3 mr.py init "<question>"` then follow `python3 mr.py next`. See `AGENTS.md` for Codex.

## Output

`reviews/<slug>/report/index.html` and `report.md`: an **AI-generated draft**. Every judgement was made by
an AI and must be verified by the review team. `analysis/results.json` holds every number.

## Limits

Free sources only (no Embase, Scopus, Web of Science or CENTRAL, which need subscriptions). Open-access full texts only, unless you add PDFs. No
conversion of medians/IQRs to means. GRADE indirectness is not rated. No human sign-off is implied.
