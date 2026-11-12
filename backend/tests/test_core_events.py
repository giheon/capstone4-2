from privframe.core.settings import CoreSettings
from privframe.harness import smoke


def test_a_case_shaped_graph_fills_every_lab_event_kind(tmp_path):
    smoke._OUT = None
    result = smoke.smoke_stream(CoreSettings(PRIVFRAME_DATA_DIR=tmp_path))
    assert result.status == "pass", result.details
    assert result.details["missing"] == []
    nodes = set(result.details["nodes"])
    # ids the topology knows: agent model turn, one node per tool call, steps inside the perceive subgraph
    assert {"legal_analyst/model", "legal_analyst/search_law", "legal_analyst/ask_user", "perceive/describe_segments"} <= nodes
    assert result.details["kinds"]["interrupt"] == 1  # an interrupt surfaces once, at the innermost node
