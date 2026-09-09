from types import SimpleNamespace

from digital_company.computer_use import BrowserMissionRunner


class FakeResponses:
    def __init__(self, responses):
        self.items = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.items)


def response(*actions, text=""):
    output = (
        []
        if not actions
        else [
            SimpleNamespace(
                type="computer_call",
                call_id="call-1",
                actions=list(actions),
                pending_safety_checks=[],
            )
        ]
    )
    return SimpleNamespace(id="response-1", output=output, output_text=text)


def action(kind, **kwargs):
    return {"type": kind, **kwargs}


def runtime(checkpoint=False):
    calls = []

    def request(method, path, payload=None):
        calls.append((method, path, payload))
        if path.endswith("/screenshot"):
            return b"png", "image/png"
        if method == "GET":
            return (
                b'{"url":"https://example.com","human_checkpoint":%s}'
                % (b"true" if checkpoint else b"false")
            ), "application/json"
        return b"{}", "application/json"

    return request, calls


def test_safe_action_is_executed_and_audited():
    request, calls = runtime()
    events = []
    fake = SimpleNamespace(
        responses=FakeResponses(
            [
                response(action("click", x=20, y=30)),
                response(text="Found the pricing page"),
            ]
        )
    )
    outcome = BrowserMissionRunner(request, fake, reporter=lambda *item: events.append(item)).run(
        "acme", "inspect public pricing", 3
    )
    assert outcome.status == "completed"
    assert any(call[2] == {"x": 20, "y": 30, "kind": "click"} for call in calls)
    assert any(event[0] == "browser.mission_action" for event in events)


def test_login_checkpoint_stops_before_another_model_turn():
    request, _ = runtime(checkpoint=True)
    fake_responses = FakeResponses([response(action("click", x=20, y=30))])
    outcome = BrowserMissionRunner(request, SimpleNamespace(responses=fake_responses)).run(
        "acme", "inspect account", 3
    )
    assert outcome.status == "waiting_human"
    assert "Login" in outcome.summary
    assert len(fake_responses.calls) == 0


def test_submit_key_and_model_safety_check_fail_closed():
    request, calls = runtime()
    fake = SimpleNamespace(responses=FakeResponses([response(action("keypress", keys=["ENTER"]))]))
    outcome = BrowserMissionRunner(request, fake).run("acme", "submit form", 3)
    assert outcome.status == "waiting_human"
    assert not any(call[0] == "POST" for call in calls)

    guarded_call = SimpleNamespace(
        type="computer_call",
        call_id="call-2",
        actions=[],
        pending_safety_checks=[{"id": "x"}],
    )
    guarded = SimpleNamespace(
        responses=FakeResponses([SimpleNamespace(id="r", output=[guarded_call], output_text="")])
    )
    assert (
        BrowserMissionRunner(request, guarded).run("acme", "continue", 3).status == "waiting_human"
    )


def test_step_limit_is_deterministic():
    request, _ = runtime()
    fake = SimpleNamespace(
        responses=FakeResponses(
            [
                response(action("screenshot")),
                response(action("screenshot")),
            ]
        )
    )
    outcome = BrowserMissionRunner(request, fake).run("acme", "observe", 1)
    assert outcome.status == "step_limit"
    assert outcome.steps == 1


def test_pause_interrupts_before_browser_action():
    request, calls = runtime()
    fake = SimpleNamespace(responses=FakeResponses([response(action("click", x=20, y=30))]))
    outcome = BrowserMissionRunner(request, fake, control_state=lambda: "paused").run(
        "acme", "inspect pricing", 3
    )
    assert outcome.status == "paused"
    assert not any(call[0] == "POST" for call in calls)
