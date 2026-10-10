"""metareview: a scripted pipeline for autonomous systematic reviews with meta-analysis.

Standard library only. AI agents read papers and propose judgements; this package does every
calculation, every validation and every count, and writes the report from stored data only.
"""
__version__ = '0.3.0'


class PipelineError(Exception):
    """A user-facing error: the message says what is wrong and what to do."""
