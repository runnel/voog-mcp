"""Tests for build_list_params — the shared list-filter query-param builder.

Added in T9 (v1.3 pre-release) when pages/_build_pages_list_params,
articles/_build_articles_list_params, and the inline filter loop in
elements/_elements_list were extracted into a single unified helper.
"""

import unittest

from voog.mcp.tools._helpers import build_list_params


class TestBuildListParamsEmpty(unittest.TestCase):
    """Empty / no-op cases."""

    def test_empty_arguments_returns_empty_dict(self):
        result = build_list_params({})
        self.assertEqual(result, {})

    def test_empty_arguments_with_plain_returns_empty(self):
        result = build_list_params({}, plain=("page_id", "language_id"))
        self.assertEqual(result, {})

    def test_empty_arguments_with_q_map_returns_empty(self):
        result = build_list_params({}, q_map={"title": "q.page.title"})
        self.assertEqual(result, {})

    def test_all_params_specified_but_absent_returns_empty(self):
        result = build_list_params(
            {},
            plain=("page_id",),
            q_map={"language_code": "q.page.language_code"},
            sort_target="s",
        )
        self.assertEqual(result, {})


class TestBuildListParamsPlain(unittest.TestCase):
    """Plain forwarding — arg name == Voog query-param name."""

    def test_single_plain_arg_forwarded(self):
        result = build_list_params({"page_id": 42}, plain=("page_id",))
        self.assertEqual(result, {"page_id": 42})

    def test_multiple_plain_args_forwarded(self):
        result = build_list_params(
            {"page_id": 7, "language_id": 99},
            plain=("page_id", "language_id"),
        )
        self.assertEqual(result, {"page_id": 7, "language_id": 99})

    def test_plain_arg_not_in_arguments_omitted(self):
        # Only page_id provided — language_id absent from arguments
        result = build_list_params({"page_id": 5}, plain=("page_id", "language_id"))
        self.assertIn("page_id", result)
        self.assertNotIn("language_id", result)

    def test_plain_arg_with_none_value_omitted(self):
        # key present but None — should be skipped
        result = build_list_params({"page_id": None}, plain=("page_id",))
        self.assertNotIn("page_id", result)
        self.assertEqual(result, {})

    def test_plain_string_arg_forwarded_as_is(self):
        result = build_list_params({"language_code": "et"}, plain=("language_code",))
        self.assertEqual(result, {"language_code": "et"})

    def test_plain_tuple_with_all_eight_elements_filters(self):
        # elements use case: 8-field plain tuple
        filters = (
            "page_id",
            "language_id",
            "language_code",
            "element_definition_id",
            "element_definition_title",
            "page_path",
            "page_path_prefix",
            "include_values",
        )
        args = {"page_id": 7, "language_code": "et", "element_definition_id": 3}
        result = build_list_params(args, plain=filters)
        self.assertEqual(
            result,
            {"page_id": 7, "language_code": "et", "element_definition_id": 3},
        )


class TestBuildListParamsQMap(unittest.TestCase):
    """q_map forwarding — arg name → Voog filter key (q.<resource>.<field>)."""

    def test_single_q_map_entry_forwarded(self):
        result = build_list_params(
            {"language_code": "et"},
            q_map={"language_code": "q.page.language_code"},
        )
        self.assertEqual(result, {"q.page.language_code": "et"})

    def test_multiple_q_map_entries_forwarded(self):
        result = build_list_params(
            {"language_code": "en", "content_type": "blog"},
            q_map={
                "language_code": "q.page.language_code",
                "content_type": "q.page.content_type",
                "node_id": "q.page.node_id",
            },
        )
        self.assertEqual(
            result,
            {
                "q.page.language_code": "en",
                "q.page.content_type": "blog",
            },
        )

    def test_q_map_entry_absent_from_arguments_omitted(self):
        result = build_list_params(
            {},
            q_map={"language_code": "q.page.language_code"},
        )
        self.assertEqual(result, {})

    def test_q_map_entry_with_none_value_omitted(self):
        result = build_list_params(
            {"language_code": None},
            q_map={"language_code": "q.page.language_code"},
        )
        self.assertEqual(result, {})

    def test_q_map_integer_value_preserved(self):
        # node_id is an integer in pages_list — must NOT be stringified
        # (Voog receives the Python integer, serialised by urllib)
        result = build_list_params(
            {"node_id": 42},
            q_map={"node_id": "q.page.node_id"},
        )
        self.assertEqual(result, {"q.page.node_id": 42})

    def test_q_map_article_filter_keys(self):
        # articles_list q-prefix variant (no q_map — articles uses plain only)
        # This test documents the articles pattern via plain, not q_map.
        result = build_list_params(
            {"page_id": 10, "language_code": "en"},
            plain=("page_id", "language_code", "language_id", "tag"),
        )
        self.assertEqual(result, {"page_id": 10, "language_code": "en"})


class TestBuildListParamsSort(unittest.TestCase):
    """sort_key + sort_target handling."""

    def test_sort_forwarded_to_s(self):
        result = build_list_params(
            {"sort": "page.title.$asc"},
            sort_target="s",
        )
        self.assertEqual(result, {"s": "page.title.$asc"})

    def test_sort_absent_no_s_key(self):
        result = build_list_params({}, sort_target="s")
        self.assertNotIn("s", result)
        self.assertEqual(result, {})

    def test_sort_target_none_means_no_sort_handling(self):
        # sort_target=None (default): even if "sort" key is in arguments,
        # it must NOT be forwarded anywhere.
        result = build_list_params({"sort": "page.title.$asc"})
        self.assertNotIn("sort", result)
        self.assertNotIn("s", result)
        self.assertEqual(result, {})

    def test_custom_sort_key_forwarded(self):
        # Allows a different input arg name if ever needed.
        result = build_list_params(
            {"order": "article.created_at.$desc"},
            sort_key="order",
            sort_target="s",
        )
        self.assertEqual(result, {"s": "article.created_at.$desc"})

    def test_sort_none_value_omitted(self):
        result = build_list_params({"sort": None}, sort_target="s")
        self.assertNotIn("s", result)


class TestBuildListParamsMix(unittest.TestCase):
    """Combinations of plain + q_map + sort."""

    def test_pages_list_full_combo(self):
        # Reproduce exact pages_list multi-filter test case from test_tools_pages.py
        result = build_list_params(
            {
                "content_type": "blog",
                "search": "leather",
                "sort": "page.created_at.$desc",
            },
            plain=("path_prefix", "search", "parent_id", "language_id"),
            q_map={
                "language_code": "q.page.language_code",
                "content_type": "q.page.content_type",
                "node_id": "q.page.node_id",
            },
            sort_target="s",
        )
        self.assertEqual(
            result,
            {
                "q.page.content_type": "blog",
                "search": "leather",
                "s": "page.created_at.$desc",
            },
        )

    def test_articles_list_full_combo(self):
        # Reproduce articles_list combined-filters test case from test_tools_articles.py
        result = build_list_params(
            {
                "page_id": 42,
                "language_code": "en",
                "tag": "news",
                "sort": "article.created_at.$desc",
            },
            plain=("page_id", "language_code", "language_id", "tag"),
            sort_target="s",
        )
        self.assertEqual(
            result,
            {
                "page_id": 42,
                "language_code": "en",
                "tag": "news",
                "s": "article.created_at.$desc",
            },
        )

    def test_only_sort_in_arguments(self):
        result = build_list_params(
            {"sort": "page.title.$asc"},
            plain=("path_prefix", "search"),
            q_map={"language_code": "q.page.language_code"},
            sort_target="s",
        )
        self.assertEqual(result, {"s": "page.title.$asc"})


class TestBuildListParamsMutation(unittest.TestCase):
    """Original arguments dict must not be mutated."""

    def test_original_dict_not_mutated(self):
        original = {"page_id": 7, "sort": "page.title.$asc", "language_code": "et"}
        original_copy = dict(original)
        build_list_params(
            original,
            plain=("page_id",),
            q_map={"language_code": "q.page.language_code"},
            sort_target="s",
        )
        self.assertEqual(original, original_copy)

    def test_returns_new_dict(self):
        arguments = {"page_id": 7}
        result = build_list_params(arguments, plain=("page_id",))
        self.assertIsNot(result, arguments)


class TestBuildListParamsStringification(unittest.TestCase):
    """Values are forwarded as-is (no coercion) — Voog handles serialisation."""

    def test_int_value_stays_int(self):
        result = build_list_params({"page_id": 42}, plain=("page_id",))
        self.assertIsInstance(result["page_id"], int)
        self.assertEqual(result["page_id"], 42)

    def test_bool_value_forwarded_as_bool(self):
        # include_values=True is forwarded as-is per the elements_list pattern
        result = build_list_params({"include_values": True}, plain=("include_values",))
        self.assertIs(result["include_values"], True)

    def test_string_value_stays_string(self):
        result = build_list_params({"language_code": "et"}, plain=("language_code",))
        self.assertIsInstance(result["language_code"], str)
        self.assertEqual(result["language_code"], "et")


if __name__ == "__main__":
    unittest.main()
