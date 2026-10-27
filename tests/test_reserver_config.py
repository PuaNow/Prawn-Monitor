import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_phase1_configs_are_local_and_do_not_ask_for_a_gpu():
    hub = _load("hub.reserver.json")
    assert hub["tanks"][0]["node_url"] == "http://127.0.0.1:8090"
    assert hub["tanks"][0]["volume_gal"] == 225
    assert hub["tanks"][0]["id"] == "nursery"

    compose_hub = _load("hub.reserver.compose.json")
    assert compose_hub["tanks"][0]["node_url"] == "http://node:8090"
    assert compose_hub["data_dir"] == "/data"

    simulated = _load("node.reserver.simulated.json")
    assert simulated["sensors"]["mode"] == "simulated"
    assert simulated["camera"]["driver"] == "simulated"
    assert simulated["port"] == 8090

    compose_node = _load("node.reserver.compose.json")
    assert compose_node["sensors"]["mode"] == "simulated"
    assert compose_node["host"] == "0.0.0.0"

    usb = _load("node.reserver.usb.example.json")
    assert usb["sensors"]["mode"] == "usb"
    assert usb["sensors"]["dissolved_oxygen"]["driver"] == "atlas_uart"
    assert usb["sensors"]["ph"]["driver"] == "atlas_uart"
    assert usb["sensors"]["temperature"]["driver"] == "atlas_uart"
    assert usb["sensors"]["water_level"]["driver"] == "usb_line"
    assert usb["camera"]["driver"] == "v4l2"
    assert usb["recording"]["enabled"] is False

    line = _load("node.reserver.usb-line.example.json")
    assert line["sensors"]["mode"] == "usb"
    assert line["sensors"]["usb_line"]["port"].startswith("/dev/serial/by-id/")
    assert "driver" not in line["sensors"].get("dissolved_oxygen", {})

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "node.reserver.compose.json" in compose
    assert "hub.reserver.compose.json" in compose
    assert "nvidia" not in compose.lower()
    assert "gpus:" not in compose
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ffmpeg" in dockerfile
    assert "nvidia/cuda" not in dockerfile.lower()
    service = (ROOT / "deploy" / "prawn-monitor-reserver.service").read_text(encoding="utf-8")
    assert "reserver-up.sh --hardware" in service
    assert "@ROOT@" in service
