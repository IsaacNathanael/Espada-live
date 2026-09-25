from __future__ import annotations

from espada.live_operations_server import LiveOperationsHandler


def test_public_server_allows_only_operator_and_generated_live_outputs() -> None:
    allowed = LiveOperationsHandler._static_route_allowed
    assert allowed("/operator/live_command/index.html")
    assert allowed("/operator/live_command/app.js?v=1")
    assert allowed("/out/live_operations/analysis/case/dossier.html")

    assert not allowed("/.env")
    assert not allowed("/.git/config")
    assert not allowed("/data/cache/private.json")
    assert not allowed("/src/espada/live_operations_server.py")
    assert not allowed("/operator/live_command/../../.env")
    assert not allowed("/operator/live_command/%2e%2e/%2e%2e/.env")
    assert not allowed("/operator/live_command/%5c..%5c..%5c.env")
