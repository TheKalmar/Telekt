from digital_company.preflight import evaluate_runtime_preflight


def test_preflight_projection_keeps_optional_services_as_warnings():
    settings = {
        "model_mode": "local",
        "local_connection_id": "local",
        "cloud_connection_id": "cloud",
    }
    connections = [
        {"id": "local", "name": "Local", "location": "local"},
        {"id": "cloud", "name": "Remote", "location": "cloud"},
    ]

    def check(connection, verify):
        if connection["id"] == "local":
            assert verify is True
            return True, "local ready"
        assert verify is False
        return False, "credential missing"

    result = evaluate_runtime_preflight(
        settings,
        worker={"status": "online"},
        connections=connections,
        browser={"status": "offline"},
        connection_check=check,
    )

    assert result["ready"] is True
    assert [item["status"] for item in result["checks"]] == [
        "pass", "pass", "warn", "warn", "warn",
    ]


def test_preflight_omits_browser_when_no_agent_has_the_plugin():
    result = evaluate_runtime_preflight(
        {
            "model_mode": "cloud",
            "local_connection_id": "local",
            "cloud_connection_id": "cloud",
        },
        worker={"status": "online"},
        connections=[{"id": "cloud", "name": "Remote", "location": "cloud"}],
        browser={"status": "disabled"},
        browser_required=False,
        connection_check=lambda _connection, _verify: (True, "ready"),
    )

    assert all(check["id"] != "browser" for check in result["checks"])
    assert result["ready"] is True
