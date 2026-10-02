from ags.ui.format import to_markdown


def test_scalars_become_labelled_bullets_with_readable_keys():
    assert to_markdown({"seasonality_alignment": "typical", "conviction": 3}) == (
        "- **Seasonality alignment:** typical\n- **Conviction:** 3"
    )


def test_nested_mappings_indent_under_their_label():
    assert to_markdown({"trend": {"short": {"state": "up", "age": 55}}}) == (
        "- **Trend:**\n  - **Short:**\n    - **State:** up\n    - **Age:** 55"
    )


def test_lists_of_scalars_are_joined_inline_and_lists_of_mappings_become_sub_bullets():
    result = to_markdown(
        {
            "supporting_analysts": ["news", "weather"],
            "key_drivers": [{"driver": "USDA stocks", "source": "news"}, {"driver": "Exports", "source": "supply_demand"}],
        }
    )

    assert result == (
        "- **Supporting analysts:** news, weather\n"
        "- **Key drivers:**\n"
        "  - **Driver:** USDA stocks · **Source:** news\n"
        "  - **Driver:** Exports · **Source:** supply_demand"
    )


def test_empty_values_none_and_floats_are_rendered_plainly():
    assert to_markdown({"error": None, "headlines": [], "vol": 0.16409}) == (
        "- **Error:** –\n- **Headlines:** none\n- **Vol:** 0.164"
    )


def test_no_json_syntax_leaks_into_the_output():
    result = to_markdown({"a": {"b": [1, 2]}, "c": [{"d": "x"}]})

    assert not any(ch in result for ch in '{}[]"')
