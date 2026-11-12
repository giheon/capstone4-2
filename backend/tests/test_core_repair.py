"""The output check sends a case back to the renderer when something that was to be redacted still shows
(core/graph.py: verify_output → render_original, at most MAX_REPAIRS times). The leak is scripted by the fixture."""
import json

from privframe.core.fixture import Fixture
from privframe.core.graph import MAX_REPAIRS

from conftest import manager_for


def run(settings, clip, leak_rounds):
    manager = manager_for(settings, fixture=Fixture(leak_rounds=leak_rounds))
    case = manager.create(clip, "clip.mp4", memo="purpose.publication_scope=public", autopilot=True)
    status = manager.wait(case, timeout=120)
    state = manager.state(case)
    renders = sum(1 for e in case.events if e["kind"] == "node.start" and e["node"] == "render_original")
    regions = json.loads((case.dir / "regions.json").read_text(encoding="utf-8"))
    manager.close()
    return status, state, renders, regions, case


def test_a_leak_found_once_is_covered_and_the_case_completes(workspace):  # noqa: F811
    settings, clip = workspace
    status, state, renders, regions, case = run(settings, clip, leak_rounds=1)
    assert status == "COMPLETED" and renders == 2 and state["repair_rounds"] == 1
    first = next(d for d in state["plan"]["decisions"] if d["method"] == "mosaic")
    extra = [r for r in regions["0"] if r[5] == first["id"]]
    assert len(extra) == 2  # the tracked region and the one added where the leak was seen
    assert any(e["kind"] == "edge" and e.get("edge") == "verify_output->render_original" for e in case.events)
    scan = next(row for row in state["verification"] if row["check"].startswith("모자이크한 대상의 노출"))
    assert scan["verdict"] in ("pass", "info")  # the second check found nothing


def test_a_leak_that_stays_fails_the_case_after_the_allowed_repairs(workspace):  # noqa: F811
    settings, clip = workspace
    status, state, renders, _, case = run(settings, clip, leak_rounds=MAX_REPAIRS + 1)
    assert status == "FAILED" and renders == MAX_REPAIRS + 1 and state["repair_rounds"] == MAX_REPAIRS
    scan = next(row for row in state["verification"] if row["check"].startswith("모자이크한 대상의 노출"))
    assert scan["verdict"] == "fail" and "1 / " in scan["output"]
    assert "모자이크한 대상의 노출" in case.detail["error"]
