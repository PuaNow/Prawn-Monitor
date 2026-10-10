import json
import urllib.request

from prawn_monitor.demo import DemoStack, run_check
from prawn_monitor.hub import get_json, post_json


def test_simulated_farm_round_trip(tmp_path):
    report = run_check(tmp_path)
    assert report["ok"], report["problems"]
    assert report["nursery_volume"] == 225
    assert report["tracking"]["mean_speed_mm_s"] == 50


def _put_json(url, payload):
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def test_sensor_offline_and_node_offline_reach_the_hub(tmp_path):
    stack = DemoStack(tmp_path)
    stack.start()
    try:
        hub = stack.urls["hub"]
        _put_json(hub + "/api/thresholds?tank=nursery", {"offline": {"node_s": 0}})
        post_json(
            stack.urls["nursery"] + "/api/simulate",
            {"offline": ["dissolved_oxygen"]},
            timeout=5,
        )
        stack.hub.poll_once()
        inbox = get_json(hub + "/api/alerts?tank=nursery", timeout=5)["alerts"]
        kinds = {row["kind"]: row["severity"] for row in inbox}
        assert kinds["sensor_offline:dissolved_oxygen"] == "critical"
        assert "dissolved_oxygen" not in kinds
        for _app, http in stack.nodes:
            http.close()
        stack.hub.poll_once()
        offline = get_json(hub + "/api/alerts?tank=nursery", timeout=5)["alerts"]
        assert any(row["kind"] == "node_offline" and row["severity"] == "critical" for row in offline)
    finally:
        stack.stop()
