"""Shared test helpers for voog_mcp tests.

Private to the test tree — the leading underscore keeps unittest discover's
default ``test*.py`` pattern from picking it up as a test module.

Centralizes ``_ann_get`` after it ended up duplicated across 8 test files,
half with the correct ``in``-membership version (PR #32 fix) and half with
the regressed ``or``-chain version that swallows explicit ``False``
annotations when the surface is a dict. One place to fix means future drift
is impossible.
"""


def _ann_get(ann, key_camel, key_snake):
    """Read an MCP annotation value across all three surface shapes.

    Tool ``annotations`` may surface as a Pydantic model (snake_case
    attributes), as a plain Python object with camelCase attributes, or
    as a dict (either casing). Tests assert ``assertIs(..., False)`` to
    pin explicit-False annotations, so this MUST distinguish "key absent"
    (returns ``None``) from "key present with value ``False``".

    NB: explicit ``in`` membership on the dict path — ``False or X``
    would swallow an explicit ``False`` annotation. Mirrors PR #32 fix.
    """
    if hasattr(ann, key_snake):
        return getattr(ann, key_snake)
    if hasattr(ann, key_camel):
        return getattr(ann, key_camel)
    if isinstance(ann, dict):
        if key_camel in ann:
            return ann[key_camel]
        if key_snake in ann:
            return ann[key_snake]
    return None
