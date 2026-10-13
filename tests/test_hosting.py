"""Hosting files stay wired together (string checks; no yaml dependency)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compose_publishes_public_port_from_env():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"${WEB_PORT:-8080}:${WEB_PORT:-8080}"' in text
    assert '"${LOCAL_WEB_PORT:-8081}:${LOCAL_WEB_PORT:-8081}"' in text


def test_installer_options_and_ctid_refusal():
    text = (ROOT / "scripts" / "install-proxmox.sh").read_text(encoding="utf-8")
    assert "HWADDR" in text and "WEB_PORT" in text
    assert "already exists" in text
