from myaibot.self_improvement.proposals import ChangeProposal, SelfImprovementGate


def test_self_improvement_gate_rejects_raw_data_mutation():
    proposal = ChangeProposal(
        proposer_agent_id="lab.manager",
        affected_paths=["data/raw/prices.csv"],
        hypothesis="improve signal",
        validation_plan="validate on 2025 only",
        expected_metric="net return",
    )
    issues = SelfImprovementGate().review(proposal)
    assert any(issue.code == "FORBIDDEN_PATH" for issue in issues)


def test_self_improvement_gate_rejects_frozen_test_peeking():
    proposal = ChangeProposal(
        proposer_agent_id="lab.manager",
        affected_paths=["labs/relay/config.yaml"],
        hypothesis="Worked on Jan 2026 OOS so promote it",
        validation_plan="use 2025 only next time",
        expected_metric="net return",
    )
    issues = SelfImprovementGate().review(proposal)
    assert any(issue.code == "FROZEN_TEST_PEEKING" for issue in issues)
