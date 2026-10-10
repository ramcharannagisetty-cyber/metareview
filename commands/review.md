---
description: Run an autonomous systematic review with meta-analysis on a question (or continue the active one)
argument-hint: <research question>
---

Follow the metareview script in `${CLAUDE_PLUGIN_ROOT}/SKILL.md` exactly.

If `$ARGUMENTS` is non-empty, start with `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" init "$ARGUMENTS" --reviewers single`.
Otherwise continue the active review with `python3 "${CLAUDE_PLUGIN_ROOT}/mr.py" next`.

Keep going, step after step, until `NEXT:` says `DONE`. You are the single agent: do every screening,
extraction, risk-of-bias and critique task yourself as the engine lists them (no subagents). Do not stop
to ask the researcher questions; make protocol decisions yourself and record them in protocol.json.
