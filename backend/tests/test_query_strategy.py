from app.services.query_strategy import classify_query_strategy


def test_overview_count_query_is_multi_hop_strategy() -> None:
    strategy = classify_query_strategy("khung CSDL gis hạ thế có mấy lớp thuộc tính")

    assert "overview_summary" in strategy.strategies
    assert "count_list" in strategy.strategies
    assert "multi_hop" in strategy.strategies
    assert strategy.requires_overview_context is True
    assert strategy.requires_diversity is True


def test_exact_lookup_uses_clear_identifier_terms_not_substrings() -> None:
    strategy = classify_query_strategy("tham khảo quy trình xử lý hồ sơ")

    assert "exact_lookup" not in strategy.strategies

    identifier_strategy = classify_query_strategy("mã văn bản 123/EVNCPC")

    assert "exact_lookup" in identifier_strategy.strategies



def test_table_detail_strategy_is_domain_neutral() -> None:
    strategy = classify_query_strategy("bảng này có những cột thuộc tính nào")

    assert "table_detail" in strategy.strategies
    assert "table summary" in strategy.search_terms
    assert all("gis" not in term.casefold() for term in strategy.search_terms)
