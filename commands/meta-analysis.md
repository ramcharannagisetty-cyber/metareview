---
description: Conduct a meta-analysis in eight steps (question, search, effect size, method, software, coding, analysis, reporting) from a question or from a coding sheet CSV
argument-hint: <research question> | <path/to/coding_sheet.csv> --measure RR|OR|HR|MD|SMD|r
---

Follow `${CLAUDE_PLUGIN_ROOT}/SKILL.md`, section "Meta-analysis".

Decide from `$ARGUMENTS`:
- It names a `.csv` file: the researcher already coded the studies. Run
  `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" ma import "<csv path>" --measure <measure> --question "<what the sheet tests>"`
  (ask nothing; infer the measure from the columns if not given: counts → RR, means → SMD, r → r, estimate+CI → the
  `measure` column or HR). Then `analyze`, `report`, critique, done.
- It is a question: run `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" init "$ARGUMENTS" --mode ma --reviewers single`
  and follow `next` to the end: the search, selection, coding (extraction) and appraisal are done by you, the
  statistics by the engine.
- It is empty: continue the active project with `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" next`.

Keep going until `NEXT:` says `DONE`. Report where `report/index.html` is and that it is an unverified AI draft.
