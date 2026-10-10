# CLAUDE.md

This folder is the metareview agent: a scripted pipeline for autonomous systematic reviews with
meta-analysis. To run a review, read `SKILL.md` and follow it; the engine's `next` command tells you
what to do at every step.

Developing it:
- Python 3.10+ standard library only in `pipeline/` (optional `pypdf` for PDFs is imported lazily).
- Tests: `python3 -m unittest discover -s tests -v` (offline; PubMed is parsed from fixtures).
- Statistics are checked against metafor's published BCG vaccine results in `tests/test_meta.py`.
