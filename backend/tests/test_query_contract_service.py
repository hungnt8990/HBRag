from app.services.queries.query_contract_service import QueryContractService


def test_query_contract_detects_identifier_lookup() -> None:
    contract = QueryContractService().build_contract("3113 la gi?")

    assert contract.detected_intent == "identifier_lookup"
    assert contract.preferred_artifact_types[:2] == ["identifier_lookup", "document_profile"]
    assert contract.output_shape == "short_fact"
    assert contract.exact_terms == ["3113"]


def test_query_contract_detects_person_assignment() -> None:
    contract = QueryContractService().build_contract("Nguyen Quang Lam tham gia mang nao?")

    assert contract.detected_intent == "person_assignment"
    assert "person_assignment_artifact" in contract.preferred_artifact_types
    assert contract.output_shape == "table"


def test_query_contract_detects_policy_rule_lookup() -> None:
    contract = QueryContractService().build_contract("Con de ket hon duoc nghi may ngay co huong luong?")

    assert contract.detected_intent == "policy_rule_lookup"
    assert contract.preferred_artifact_types[0] == "policy_rule_artifact"


def test_query_contract_detects_procedure_lookup() -> None:
    contract = QueryContractService().build_contract("Thu tuc cap dien moi can ho so va le phi gi?")

    assert contract.detected_intent == "procedure_lookup"
    assert "procedure_artifact" in contract.preferred_artifact_types


def test_query_contract_rules_are_configurable() -> None:
    service = QueryContractService(rules={"policy_rule_patterns": [r"\bcustom-benefit\b"]})

    contract = service.build_contract("custom-benefit applies to which group?")

    assert contract.detected_intent == "policy_rule_lookup"

