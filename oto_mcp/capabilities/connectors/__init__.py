"""Governance capabilities of a connector (ADR 0019/0024/0044): enabling it,
instantiating it, sharing it, probing it, listing its identities.

A package with no surface of its own — `capabilities/__init__.py` imports each module for
its DECLARATION side effect. ⚠️ Not to be confused with `oto_mcp/connectors/`, which holds
the machinery; here these are the VERBS exposed to both faces.
"""
