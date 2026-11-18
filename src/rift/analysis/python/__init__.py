"""Python analysis: static extraction and resolution of Python source with the stdlib ast module.

Entry points live in :mod:`rift.analysis.python.analyzer`. Analyzed code is never imported,
executed or compiled to bytecode (SPEC §9, §21).
"""
