# metareview for Codex and other agents

Read `SKILL.md` and follow it. The entry point is `python3 mr.py <command>` run from this folder (or
`MR_HOME=<dir>` to choose where reviews are stored).

Where the engine says "launch a fresh subagent", run a separate, tool-free child that cannot see your
context, for example:

```
codex exec --skip-git-repo-check -s workspace-write "<role prompt from agents/<name>.md> + the paths from the NEXT line>"
```

Reviewer A and reviewer B must be separate children. Never do a screening, extraction or appraisal
yourself in the lead context, and never edit files under `reviews/` except `protocol.json`.
