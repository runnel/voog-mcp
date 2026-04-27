"""Tests for the shared _ann_get helper.

The whole reason this helper was lifted to a shared module was that the
``False or X`` regression slipped into 4 of 8 duplicates. These tests
pin the three surface shapes (Pydantic-model, plain-object, dict) AND
the False-vs-absent distinction so the regression can't come back.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._test_helpers import _ann_get


class _Camel:
    def __init__(self):
        self.readOnlyHint = True
        self.destructiveHint = False


class _Snake:
    def __init__(self):
        self.read_only_hint = True
        self.destructive_hint = False


class TestAnnGet(unittest.TestCase):
    def test_snake_attribute_preferred(self):
        # Pydantic model surface — snake_case is the canonical Python form
        ann = _Snake()
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)

    def test_camel_attribute_fallback(self):
        # Plain object with camelCase attrs (some MCP SDK shapes)
        ann = _Camel()
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)

    def test_dict_camel_key(self):
        ann = {"readOnlyHint": True, "destructiveHint": False}
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)

    def test_dict_snake_key(self):
        ann = {"read_only_hint": True, "destructive_hint": False}
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), True)
        self.assertIs(_ann_get(ann, "destructiveHint", "destructive_hint"), False)

    def test_dict_explicit_false_not_swallowed(self):
        # The whole point of the PR #32 fix — dict.get(camel) or dict.get(snake)
        # would return None for an explicit False, breaking assertIs(..., False)
        ann = {"readOnlyHint": False}
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)

    def test_dict_explicit_false_under_snake_key_not_swallowed(self):
        ann = {"read_only_hint": False}
        self.assertIs(_ann_get(ann, "readOnlyHint", "read_only_hint"), False)

    def test_missing_key_returns_none(self):
        # Distinguishes "absent" from "present with False"
        ann = {"someOtherHint": True}
        self.assertIsNone(_ann_get(ann, "readOnlyHint", "read_only_hint"))

    def test_empty_dict_returns_none(self):
        self.assertIsNone(_ann_get({}, "readOnlyHint", "read_only_hint"))

    def test_none_annotation_returns_none(self):
        self.assertIsNone(_ann_get(None, "readOnlyHint", "read_only_hint"))


if __name__ == "__main__":
    unittest.main()
