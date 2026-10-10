---
description: Conduct a systematic review (question → protocol → search → selection → extraction → appraisal → narrative synthesis → report), following the standard nine steps
argument-hint: <research question>
---

Follow `${CLAUDE_PLUGIN_ROOT}/SKILL.md`, section "Systematic review".

If `$ARGUMENTS` is non-empty, start with
`python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" init "$ARGUMENTS" --mode sr --reviewers single`.
Otherwise continue the active review with `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" next`.

You are the single agent: do every screening, extraction, risk-of-bias and critique task yourself as the
engine lists them. Keep going until `NEXT:` says `DONE`. The result is a systematic review with a
qualitative (narrative) synthesis and no pooled estimate; add `--with-meta-analysis` to `init` if the
researcher asked for pooling as well.
