"""The HTTP surface, driven end to end with a scripted model.

These go through the real routes, the real graph and the real SQLite
checkpointer — only the model is stubbed. The point is to catch the things
unit tests miss: that state survives between requests, that an edit is
recorded as a correction, and that no route submits anything.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app import main
from app.config import Settings
from tests.scripted import ScriptedClient


@pytest.fixture(autouse=True)
def no_leaked_runtime_key():
    """The entered key is process-global; it must not leak between tests."""
    app_config.clear_runtime_api_key()
    yield
    app_config.clear_runtime_api_key()


@pytest.fixture
def data_dir(tmp_path):
    data = tmp_path / "data"
    (data / "evidence").mkdir(parents=True)
    (data / "objective.md").write_text(
        "# Objective\n\n"
        "| Field | Value |\n|---|---|\n"
        "| `Objective_ID` | OBJ-TEST-01 |\n"
        "| `Title` | Do a difficult thing |\n"
        "| `Success_Measure` | Three of the thing |\n"
        "| `Target_Completion` | 2026-09-30 |\n",
        encoding="utf-8",
    )
    (data / "prior_update.md").write_text(
        "# Prior\n\n"
        "| Field | Value |\n|---|---|\n"
        "| `Quarter` | 2026-Q2 |\n"
        "| `Traffic_Light` | **Green** |\n"
        "| `Progress_Percent` | 45 |\n"
        "| `Key_Success` | It was all going well. |\n",
        encoding="utf-8",
    )
    (data / "evidence" / "early.md").write_text(
        "# Email — 1 May 2026\n\nAn optimistic early account.", encoding="utf-8"
    )
    (data / "evidence" / "late.md").write_text(
        "# Teams — 1 August 2026\n\nA more sober later account.", encoding="utf-8"
    )
    return data


@pytest.fixture
def client(data_dir, tmp_path, monkeypatch):
    """A client whose event loop outlives a single request.

    Runs are background tasks. TestClient tears its loop down per request
    unless entered as a context manager, which would orphan them.
    """
    settings = Settings(
        data_dir=data_dir,
        runs_dir=tmp_path / "runs",
        quarter="2026-Q3",
        anthropic_api_key="not-used-by-the-stub",
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "client", lambda: ScriptedClient())
    with TestClient(main.app) as http:
        yield http, settings


def start_run(http) -> str:
    """Kick off a run and wait for it.

    Runs are background tasks now, so the redirect arrives before the draft
    does. Polling the progress endpoint also gives the event loop the chance
    to advance the task between requests.
    """
    response = http.post("/runs", data={"quarter": "2026-Q3"}, follow_redirects=False)
    assert response.status_code == 303
    thread_id = response.headers["location"].removeprefix("/runs/")

    for _ in range(200):
        if http.get(f"/runs/{thread_id}/progress.json").json()["finished"]:
            return thread_id
    raise AssertionError(f"run {thread_id} never finished")


def acknowledge_all_fields(http, thread_id: str) -> None:
    """POST every draft field once so Approve and export can unlock."""
    row = http.get(f"/runs/{thread_id}/state.json").json()["row"]
    for field, proposal in row.items():
        if not isinstance(proposal, dict) or "edited_by_director" not in proposal:
            continue
        if proposal.get("edited_by_director"):
            continue
        value = proposal.get("value")
        if field == "Support_From":
            data = [("Support_From", item) for item in (value or [])]
        elif field == "Progress_Percent":
            data = {field: "" if value is None else str(value)}
        else:
            data = {field: "" if value is None else value}
        assert http.post(f"/runs/{thread_id}/field/{field}", data=data).status_code in {
            200,
            303,
        }


# --- landing -----------------------------------------------------------------


def test_runs_home_renders(client):
    http, _ = client
    body = http.get("/").text
    assert "Quarterly update drafter" in body
    assert "New run" in body


def test_inlined_assets_are_not_html_escaped(client):
    """Row clicks and child selectors break if Jinja escapes the inlined bundle."""
    http, _ = client
    body = http.get("/").text
    assert 'closest("tr.rowlink[data-href]")' in body
    assert "&#34;tr.rowlink" not in body
    assert ".brand > span:last-child" in body
    assert ".brand &gt; span" not in body


def test_runs_table_rows_link_to_the_detail_page(client):
    http, _ = client
    body = http.get("/").text
    assert 'data-href="/runs/' in body or 'href="/runs/' in body


def test_new_run_lists_the_evidence_and_the_objective(client):
    http, _ = client
    body = http.get("/runs/new").text
    assert "OBJ-TEST-01" in body
    assert "Three of the thing" in body
    assert "E1" in body and "E2" in body


def test_new_run_explains_a_missing_data_folder(client, tmp_path, monkeypatch):
    http, settings = client
    monkeypatch.setattr(main, "settings", settings.model_copy(update={"data_dir": tmp_path / "nope"}))
    response = http.get("/runs/new")
    assert response.status_code == 500
    assert "objective.md" in response.text


# --- generating --------------------------------------------------------------


def test_a_run_produces_a_draft_and_stages_nothing(client):
    http, settings = client
    thread_id = start_run(http)

    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["awaiting_review"] is True
    assert state["row"]["Traffic_Light"]["value"] == "Amber"
    assert state["row"]["submitted"] is False
    assert not (settings.run_dir(thread_id) / "staged_row.json").exists()


def test_review_page_shows_the_proposal_and_the_standing_notice(client):
    http, _ = client
    body = http.get(f"/runs/{start_run(http)}").text
    assert "Nothing here has been submitted" in body
    assert "Substrate-Drafted" in body
    assert "Trend vs prior quarter" in body, "the omitted field should be explained"


def test_review_page_contrasts_the_prior_quarter(client):
    http, _ = client
    body = http.get(f"/runs/{start_run(http)}").text
    assert "What changed since 2026-Q2" in body
    assert "Green" in body and "Amber" in body


def test_abstained_field_is_shown_as_needing_input(client):
    http, _ = client
    body = http.get(f"/runs/{start_run(http)}").text
    assert "needs your input" in body


def test_unknown_run_is_a_404(client):
    http, _ = client
    assert http.get("/runs/does-not-exist").status_code == 404


# --- editing -----------------------------------------------------------------


def test_editing_a_field_records_a_correction_and_persists(client):
    http, _ = client
    thread_id = start_run(http)

    response = http.post(
        f"/runs/{thread_id}/field/Key_Success",
        data={"Key_Success": "Smaller than we said."},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.status_code == 200
    assert "acknowledged" in response.text

    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Key_Success"]["value"] == "Smaller than we said."
    assert state["row"]["Key_Success"]["edited_by_director"] is True


def test_acknowledging_again_returns_the_field_to_normal_mode(client):
    http, _ = client
    thread_id = start_run(http)
    original = http.get(f"/runs/{thread_id}/state.json").json()["row"]["Progress_Percent"][
        "value"
    ]

    ack = http.post(
        f"/runs/{thread_id}/field/Progress_Percent",
        data={"Progress_Percent": "40"},
        headers={"X-Requested-With": "fetch"},
    )
    assert ack.status_code == 200
    assert 'data-field-ack="1"' in ack.text
    assert "btn-ack-done" in ack.text

    undo = http.post(
        f"/runs/{thread_id}/field/Progress_Percent",
        data={"Progress_Percent": "40"},
        headers={"X-Requested-With": "fetch"},
    )
    assert undo.status_code == 200
    assert 'data-field-ack="0"' in undo.text
    assert "btn-ack-done" not in undo.text

    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Progress_Percent"]["edited_by_director"] is False
    assert state["row"]["Progress_Percent"]["value"] == original
    assert not any(
        c["field"] == "Progress_Percent" for c in state.get("corrections", [])
    )

    acknowledge_all_fields(http, thread_id)
    assert (
        http.post(f"/runs/{thread_id}/approve", follow_redirects=False).status_code
        == 303
    )

    # Start a fresh run so we can prove clearing one ack locks export again.
    thread_id = start_run(http)
    acknowledge_all_fields(http, thread_id)
    value = http.get(f"/runs/{thread_id}/state.json").json()["row"]["Progress_Percent"][
        "value"
    ]
    cleared = http.post(
        f"/runs/{thread_id}/field/Progress_Percent",
        data={"Progress_Percent": "" if value is None else str(value)},
        headers={"X-Requested-With": "fetch"},
    )
    assert 'data-field-ack="0"' in cleared.text
    assert http.get(f"/runs/{thread_id}/state.json").json()["row"]["Progress_Percent"][
        "edited_by_director"
    ] is False

    blocked = http.post(f"/runs/{thread_id}/approve", follow_redirects=False)
    assert blocked.status_code == 400
    assert "Acknowledge every field" in blocked.text


def test_editing_the_same_field_twice_keeps_one_correction(client):
    http, settings = client
    thread_id = start_run(http)

    for value in ("First go.", "Second go."):
        http.post(
            f"/runs/{thread_id}/field/Key_Challenge",
            data={"Key_Challenge": value},
            headers={"X-Requested-With": "fetch"},
        )

    acknowledge_all_fields(http, thread_id)
    http.post(f"/runs/{thread_id}/stage", follow_redirects=False)
    lines = (settings.run_dir(thread_id) / "corrections.jsonl").read_text().strip().splitlines()
    key_challenge = [json.loads(line) for line in lines if json.loads(line)["field"] == "Key_Challenge"]
    assert len(key_challenge) == 1
    assert key_challenge[0]["director_value"] == "Second go."


def test_traffic_light_can_be_overridden_by_the_director(client):
    http, _ = client
    thread_id = start_run(http)

    http.post(
        f"/runs/{thread_id}/field/Traffic_Light",
        data={"Traffic_Light": "Red"},
        headers={"X-Requested-With": "fetch"},
    )
    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Traffic_Light"]["value"] == "Red"


def test_progress_is_clamped_to_the_valid_range(client):
    http, _ = client
    thread_id = start_run(http)

    http.post(
        f"/runs/{thread_id}/field/Progress_Percent",
        data={"Progress_Percent": "5000"},
        headers={"X-Requested-With": "fetch"},
    )
    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Progress_Percent"]["value"] == 100


def test_support_from_outside_the_vocabulary_is_dropped(client):
    http, _ = client
    thread_id = start_run(http)

    http.post(
        f"/runs/{thread_id}/field/Support_From",
        data={"Support_From": ["Finance", "NotAThing"]},
        headers={"X-Requested-With": "fetch"},
    )
    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Support_From"]["value"] == ["Finance"]


def test_editing_without_javascript_redirects_back(client):
    http, _ = client
    thread_id = start_run(http)

    response = http.post(
        f"/runs/{thread_id}/field/Key_Success",
        data={"Key_Success": "Posted without fetch."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/runs/{thread_id}")

    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Key_Success"]["value"] == "Posted without fetch."


def test_editing_an_unknown_field_is_a_404(client):
    http, _ = client
    thread_id = start_run(http)
    assert http.post(f"/runs/{thread_id}/field/Nonsense", data={}).status_code == 404


def test_trend_field_cannot_be_set_through_the_api(client):
    http, _ = client
    thread_id = start_run(http)
    response = http.post(
        f"/runs/{thread_id}/field/Trend_vs_Prior_Quarter",
        data={"Trend_vs_Prior_Quarter": "Deteriorated"},
    )
    assert response.status_code == 404

    state = http.get(f"/runs/{thread_id}/state.json").json()
    assert "Trend_vs_Prior_Quarter" not in state["row"]


# --- staging -----------------------------------------------------------------


def test_staging_writes_a_file_that_is_not_submitted(client):
    http, settings = client
    thread_id = start_run(http)
    acknowledge_all_fields(http, thread_id)

    http.post(f"/runs/{thread_id}/stage", follow_redirects=False)

    staged = json.loads((settings.run_dir(thread_id) / "staged_row.json").read_text())
    assert staged["submitted"] is False
    assert staged["Source"] == "Substrate-Drafted"
    assert "Trend_vs_Prior_Quarter" not in staged
    assert staged["thread_id"] == thread_id


def test_acknowledge_all_marks_every_field_and_unlocks_export(client):
    http, _ = client
    thread_id = start_run(http)

    body = http.get(f"/runs/{thread_id}").text
    assert "Acknowledge all" in body
    assert 'data-ack-all' in body
    footer = body.split('id="approve-footer"', 1)[1]
    assert footer.index("data-ack-all") < footer.index("data-open-approve")
    assert "footer-actions" in footer

    response = http.post(
        f"/runs/{thread_id}/acknowledge-all", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/runs/{thread_id}"

    state = http.get(f"/runs/{thread_id}/state.json").json()
    for field, proposal in state["row"].items():
        if isinstance(proposal, dict) and "edited_by_director" in proposal:
            assert proposal["edited_by_director"] is True, field

    review = http.get(f"/runs/{thread_id}").text
    assert "✓ All acknowledged" in review
    approve_btn = re.search(r"<button\b[^>]*\bdata-open-approve\b[^>]*>", review)
    assert approve_btn is not None
    assert "disabled" not in approve_btn.group(0)

    # Toggle off — clears every acknowledgement and locks export again.
    cleared = http.post(f"/runs/{thread_id}/acknowledge-all", follow_redirects=False)
    assert cleared.status_code == 303
    state = http.get(f"/runs/{thread_id}/state.json").json()
    for field, proposal in state["row"].items():
        if isinstance(proposal, dict) and "edited_by_director" in proposal:
            assert proposal["edited_by_director"] is False, field
    locked = http.get(f"/runs/{thread_id}").text
    assert "Acknowledge all" in locked
    approve_btn = re.search(r"<button\b[^>]*\bdata-open-approve\b[^>]*>", locked)
    assert approve_btn is not None
    assert "disabled" in approve_btn.group(0)
    assert http.post(f"/runs/{thread_id}/approve", follow_redirects=False).status_code == 400


def test_staging_is_blocked_until_every_field_is_acknowledged(client):
    http, settings = client
    thread_id = start_run(http)

    response = http.post(f"/runs/{thread_id}/approve", follow_redirects=False)
    assert response.status_code == 400
    assert "Acknowledge every field" in response.text
    assert not (settings.run_dir(thread_id) / "staged_row.json").exists()

    body = http.get(f"/runs/{thread_id}").text
    assert "Acknowledged" in body
    assert "Acknowledge every field above before export unlocks" in body
    approve_btn = re.search(r"<button\b[^>]*\bdata-open-approve\b[^>]*>", body)
    assert approve_btn is not None
    assert "disabled" in approve_btn.group(0)


def test_staged_row_is_downloadable_after_staging(client):
    http, _ = client
    thread_id = start_run(http)
    assert http.get(f"/runs/{thread_id}/staged.json").status_code == 404

    acknowledge_all_fields(http, thread_id)
    http.post(f"/runs/{thread_id}/stage", follow_redirects=False)
    assert http.get(f"/runs/{thread_id}/staged.json").json()["submitted"] is False


def test_review_page_says_staged_not_submitted(client):
    http, _ = client
    thread_id = start_run(http)
    acknowledge_all_fields(http, thread_id)
    http.post(f"/runs/{thread_id}/stage", follow_redirects=False)

    body = http.get(f"/runs/{thread_id}").text
    assert "waiting for you to submit it" in body
    export_btn = re.search(r"<a\b[^>]*\bdata-open-export\b[^>]*>", body)
    assert export_btn is not None
    assert "aria-disabled" not in export_btn.group(0)


def test_open_export_stays_locked_until_every_field_is_acknowledged(client):
    http, settings = client
    thread_id = start_run(http)
    acknowledge_all_fields(http, thread_id)
    assert http.post(f"/runs/{thread_id}/stage", follow_redirects=False).status_code == 303

    # Un-acknowledge one field — Open export must lock and export routes refuse.
    value = http.get(f"/runs/{thread_id}/state.json").json()["row"]["Progress_Percent"][
        "value"
    ]
    cleared = http.post(
        f"/runs/{thread_id}/field/Progress_Percent",
        data={"Progress_Percent": "" if value is None else str(value)},
        headers={"X-Requested-With": "fetch"},
    )
    assert 'data-field-ack="0"' in cleared.text

    body = http.get(f"/runs/{thread_id}").text
    export_btn = re.search(r"<a\b[^>]*\bdata-open-export\b[^>]*>", body)
    assert export_btn is not None
    assert 'aria-disabled="true"' in export_btn.group(0)
    assert "Acknowledge every field above before export unlocks" in body

    blocked = http.get(f"/runs/{thread_id}/export", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == f"/runs/{thread_id}"
    assert http.get(f"/runs/{thread_id}/staged.json").status_code == 400
    assert http.get(f"/runs/{thread_id}/export.csv").status_code == 400
    assert (settings.run_dir(thread_id) / "staged_row.json").exists()


# --- evidence ----------------------------------------------------------------


def test_evidence_page_highlights_the_cited_block(client):
    http, _ = client
    thread_id = start_run(http)

    state = http.get(f"/runs/{thread_id}/state.json").json()
    claim = state["claims"][0]

    body = http.get(
        f"/runs/{thread_id}/evidence/{claim['doc_id']}?claim={claim['claim_id']}"
    ).text
    assert "block-cited" in body, "the cited block should be marked"
    assert claim["claim_id"] in body


def test_evidence_page_without_a_claim_highlights_nothing(client):
    http, _ = client
    thread_id = start_run(http)
    body = http.get(f"/runs/{thread_id}/evidence/E1").text
    assert "block-index" in body
    # Class applied to a block — not the inlined stylesheet rule name alone.
    assert 'class="block-cited"' not in body
    assert "class='block-cited'" not in body


def test_unknown_document_is_a_404(client):
    http, _ = client
    thread_id = start_run(http)
    assert http.get(f"/runs/{thread_id}/evidence/E99").status_code == 404


# --- persistence -------------------------------------------------------------


def test_state_survives_a_new_client(client, data_dir, tmp_path, monkeypatch):
    """A director can close the tab and come back to the same draft."""
    http, settings = client
    thread_id = start_run(http)
    http.post(
        f"/runs/{thread_id}/field/Key_Success",
        data={"Key_Success": "Edited before closing the tab."},
        headers={"X-Requested-With": "fetch"},
    )

    with TestClient(main.app) as fresh:
        state = fresh.get(f"/runs/{thread_id}/state.json").json()
    assert state["row"]["Key_Success"]["value"] == "Edited before closing the tab."


# --- the key is required -----------------------------------------------------


def test_generating_without_a_key_fails_cleanly(data_dir, tmp_path, monkeypatch):
    """There is no offline mode. A canned draft that looked real would be worse."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "settings",
        Settings(data_dir=data_dir, runs_dir=tmp_path / "runs", anthropic_api_key=""),
    )
    http = TestClient(main.app, raise_server_exceptions=False)

    assert "No model connected yet" in http.get("/runs/new").text

    # A rendered explanation, not a bare JSON error: this is the first thing
    # someone hits if they open the app before setting a key.
    response = http.post("/runs", data={"quarter": "2026-Q3"})
    assert response.status_code == 400
    assert "No API key loaded" in response.text


def test_missing_key_raises_rather_than_falling_back():
    from app.config import MissingAPIKey
    from app.llm import AnthropicClient

    with pytest.raises(MissingAPIKey):
        AnthropicClient(Settings(anthropic_api_key=""))


def test_empty_support_from_is_not_flagged_as_missing_evidence(client):
    """An empty multi-select is a real answer, not a defect."""
    body = http_body(client)
    assert "No supporting evidence was cited" not in body


def http_body(client):
    http, _ = client
    return http.get(f"/runs/{start_run(http)}").text


# --- entering a key through the interface ------------------------------------


@pytest.fixture
def keyless(data_dir, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        main,
        "settings",
        Settings(data_dir=data_dir, runs_dir=tmp_path / "runs", anthropic_api_key=""),
    )
    return TestClient(main.app, raise_server_exceptions=False)


def accept_key(monkeypatch):
    async def ok(self):
        return None

    monkeypatch.setattr("app.llm.AnthropicClient.check", ok)


def reject_key(monkeypatch, message="authentication_error: invalid x-api-key"):
    async def fail(self):
        raise RuntimeError(message)

    monkeypatch.setattr("app.llm.AnthropicClient.check", fail)


def test_a_working_key_is_accepted_and_unlocks_drafting(keyless, monkeypatch):
    accept_key(monkeypatch)
    body = keyless.post("/settings/key", data={"api_key": "sk-ant-test-0123456789abcd"}).text

    assert "Key accepted" in body
    assert "entered here" in body
    assert "No model connected yet" not in body, "drafting should be unblocked"
    assert app_config.runtime_api_key() == "sk-ant-test-0123456789abcd"


def test_the_key_is_never_rendered_back_in_full(keyless, monkeypatch):
    accept_key(monkeypatch)
    secret = "sk-ant-test-0123456789abcd"
    body = keyless.post("/settings/key", data={"api_key": secret}).text

    assert secret not in body, "the key must not be echoed into the page"
    assert "sk-ant-…abcd" in body, "a masked form should be shown instead"


def test_a_rejected_key_is_not_kept(keyless, monkeypatch):
    reject_key(monkeypatch)
    response = keyless.post("/settings/key", data={"api_key": "sk-ant-wrong"})

    assert response.status_code == 400
    assert "rejected" in response.text
    assert app_config.runtime_api_key() == "", "a key that failed must not stay loaded"


def test_error_messages_are_actionable(keyless, monkeypatch):
    for raised, expected in [
        ("authentication_error", "rejected"),
        ("403 permission denied", "not permitted"),
        ("insufficient credit balance", "no available credit"),
    ]:
        reject_key(monkeypatch, raised)
        assert expected in keyless.post("/settings/key", data={"api_key": "sk-x"}).text


def test_an_empty_submission_is_rejected(keyless):
    response = keyless.post("/settings/key", data={"api_key": "   "})
    assert response.status_code == 400
    assert "Paste a key first" in response.text


def test_a_key_can_be_cleared(keyless, monkeypatch):
    accept_key(monkeypatch)
    keyless.post("/settings/key", data={"api_key": "sk-ant-test-0123456789abcd"})

    body = keyless.post("/settings/key/clear").text
    assert "cleared" in body
    assert "No model connected yet" in body
    assert app_config.runtime_api_key() == ""


def test_entered_key_takes_precedence_over_the_environment(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")
    settings = Settings(data_dir=data_dir, runs_dir=tmp_path / "runs", anthropic_api_key="")
    assert settings.resolved_api_key() == "sk-ant-from-the-environment"

    app_config.set_runtime_api_key("sk-ant-entered-in-the-browser")
    assert settings.resolved_api_key() == "sk-ant-entered-in-the-browser"


def test_masking_never_reveals_a_short_key():
    assert app_config.mask("sk-ant-0123456789abcdef").endswith("cdef")
    assert "0123456789" not in app_config.mask("sk-ant-0123456789abcdef")
    assert app_config.mask("short") == "…hort"
    assert app_config.mask("") == ""


def test_drafting_without_a_key_explains_itself(keyless):
    """A dead button is indistinguishable from a broken one."""
    response = keyless.post("/runs", data={"quarter": "2026-Q3"})
    assert response.status_code == 400
    assert "No API key loaded" in response.text


def test_the_draft_button_is_never_silently_disabled(keyless):
    body = keyless.get("/runs/new").text
    assert "No model connected yet" in body
    # Attribute on controls — not stylesheet/:disabled or aria-disabled in JS.
    assert not re.search(r"<(button|input)\b[^>]*\sdisabled\b", body, re.I)


def test_a_failed_run_shows_a_page_not_a_stack_trace(client, monkeypatch):
    """A director must never be shown a traceback."""
    http, _ = client

    class Exploding(ScriptedClient):
        async def structured(self, system, instruction, schema):
            raise RuntimeError("something deep in the graph went wrong")

    monkeypatch.setattr(main, "client", lambda: Exploding())
    redirect = http.post("/runs", data={"quarter": "2026-Q3"}, follow_redirects=False)
    thread_id = redirect.headers["location"].removeprefix("/runs/")
    for _ in range(200):
        if http.get(f"/runs/{thread_id}/progress.json").json()["finished"]:
            break

    response = http.get(f"/runs/{thread_id}")
    assert response.status_code == 500
    assert "could not be completed" in response.text
    assert "nothing was staged" in response.text.lower()
    assert "Traceback" not in response.text


# --- putting data in through the interface -----------------------------------


def test_evidence_can_be_pasted_in_and_is_read_on_the_next_run(client):
    http, settings = client

    response = http.post(
        "/evidence/add",
        data={
            "title": "Email — 30 September 2026",
            "date": "",
            "body": "The second agreement was signed yesterday.",
        },
    )
    assert response.status_code == 200
    assert "Added" in response.text
    assert "3 documents" in response.text, "the list should reflect the new document"

    # And the workflow actually reads it.
    state = http.get(f"/runs/{start_run(http)}/state.json").json()
    assert len({c["doc_id"] for c in state["claims"]}) == 3


def test_a_pasted_document_lands_in_date_order(client):
    http, _ = client
    http.post(
        "/evidence/add",
        data={"title": "A note", "date": "1 June 2026", "body": "Something."},
    )
    body = http.get("/runs/new").text
    assert "2026-06-01" in body


def test_an_upload_is_accepted(client):
    http, _ = client
    response = http.post(
        "/evidence/add",
        files={"files": ("extra.md", "# Extra — 5 July 2026\n\nBody text.".encode(), "text/markdown")},
    )
    assert "Added" in response.text
    assert "3 documents" in response.text


def test_adding_nothing_says_so(client):
    http, _ = client
    response = http.post("/evidence/add", data={"title": "", "body": ""})
    assert response.status_code == 400
    assert "Nothing to add" in response.text


def test_evidence_can_be_edited_in_place(client, data_dir):
    http, _ = client
    response = http.get("/evidence/early.md/edit")
    assert response.status_code == 200
    assert "An optimistic early account" in response.text

    saved = http.post(
        "/evidence/early.md/edit",
        data={"text": "# Email — 1 May 2026\n\nCorrected account."},
    )
    assert "Saved" in saved.text
    assert "Corrected account." in (data_dir / "evidence" / "early.md").read_text()


def test_evidence_can_be_removed(client, data_dir):
    http, _ = client
    response = http.post("/evidence/late.md/delete")

    assert "Removed" in response.text
    assert not (data_dir / "evidence" / "late.md").exists()
    assert "1 document" in response.text


def test_a_run_can_be_deleted(client, data_dir):
    http, settings = client
    thread_id = start_run(http)
    acknowledge_all_fields(http, thread_id)
    http.post(f"/runs/{thread_id}/stage", follow_redirects=False)
    assert (settings.run_dir(thread_id) / "staged_row.json").exists()

    home = http.get("/").text
    assert f'action="/runs/{thread_id}/delete"' in home
    assert 'aria-label="Delete run' in home

    response = http.post(f"/runs/{thread_id}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    assert not settings.run_dir(thread_id).exists()
    assert http.get(f"/runs/{thread_id}").status_code == 404
    assert thread_id not in http.get("/").text
    assert (data_dir / "evidence" / "early.md").exists()
    assert (data_dir / "evidence" / "late.md").exists()


def test_run_delete_refuses_reserved_ids(client, tmp_path):
    http, settings = client
    settings.understanding_dir.mkdir(parents=True, exist_ok=True)
    marker = settings.understanding_dir / "keep.txt"
    marker.write_text("stay", encoding="utf-8")

    response = http.post("/runs/understanding/delete", follow_redirects=False)
    assert response.status_code == 400
    assert marker.exists()


def test_traversal_through_the_url_is_refused(client, data_dir):
    http, _ = client
    response = http.post("/evidence/..%2Fobjective.md/delete")

    assert response.status_code in (400, 404)
    assert (data_dir / "objective.md").exists(), "the objective must survive"


def test_the_objective_can_be_edited(client, data_dir):
    http, _ = client
    response = http.post(
        "/objective",
        data={
            "Objective_ID": "OBJ-TEST-01",
            "Title": "A revised objective",
            "Success_Measure": "Two of the thing, not three",
            "Target_Completion": "2026-12-31",
        },
    )
    assert "Objective updated" in response.text
    assert "Two of the thing, not three" in response.text
    assert "Two of the thing" in (data_dir / "objective.md").read_text()


def test_an_empty_evidence_folder_is_explained_not_crashed(client, data_dir):
    http, _ = client
    for name in ("early.md", "late.md"):
        http.post(f"/evidence/{name}/delete")

    body = http.get("/runs/new").text
    assert "Nothing to read yet" in body
    assert "No evidence to read" in body


# --- watching a run happen ---------------------------------------------------


def test_progress_reports_each_stage_in_order(client):
    http, _ = client
    thread_id = start_run(http)

    events = http.get(f"/runs/{thread_id}/progress.json").json()
    assert events["finished"] is True

    stages = [e["stage"] for e in events["events"]]
    assert stages[0] == "load"
    assert stages.count("read_document") == 2, "one event per document"
    for expected in ("reconcile", "assess", "compose", "validate", "review"):
        assert expected in stages, expected
    assert stages[-1] == "done"


def test_progress_events_carry_readable_labels_and_detail(client):
    http, _ = client
    thread_id = start_run(http)
    events = http.get(f"/runs/{thread_id}/progress.json").json()["events"]

    by_stage = {e["stage"]: e for e in events}
    assert by_stage["reconcile"]["label"] == "Reconciling what the documents disagree about"
    assert by_stage["reconcile"]["detail"] == "1 conflict, 0 gaps"
    assert "1 statement," not in by_stage["read_document"]["detail"]
    assert "statement" in by_stage["read_document"]["detail"]
    assert by_stage["validate"]["detail"] == "no problems"


def test_the_page_shows_progress_while_a_run_is_in_flight(client):
    http, _ = client
    redirect = http.post("/runs", data={"quarter": "2026-Q3"}, follow_redirects=False)
    thread_id = redirect.headers["location"].removeprefix("/runs/")

    body = http.get(f"/runs/{thread_id}").text
    assert "Reading the evidence" in body or "Nothing here has been submitted" in body


def test_the_event_stream_is_server_sent_events(client):
    http, _ = client
    thread_id = start_run(http)

    with http.stream("GET", f"/runs/{thread_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payload = "".join(response.iter_text())

    assert payload.startswith("data: ")
    assert '"stage": "done"' in payload


def test_events_for_an_unknown_run_are_a_404(client):
    http, _ = client
    assert http.get("/runs/nope/events").status_code == 404
    assert http.get("/runs/nope/progress.json").status_code == 404


def test_a_failed_run_publishes_a_readable_failure(client, monkeypatch):
    http, _ = client

    class Exploding(ScriptedClient):
        async def structured(self, system, instruction, schema):
            raise RuntimeError("deep failure")

    monkeypatch.setattr(main, "client", lambda: Exploding())
    redirect = http.post("/runs", data={"quarter": "2026-Q3"}, follow_redirects=False)
    thread_id = redirect.headers["location"].removeprefix("/runs/")
    for _ in range(200):
        if http.get(f"/runs/{thread_id}/progress.json").json()["finished"]:
            break

    events = http.get(f"/runs/{thread_id}/progress.json").json()["events"]
    assert events[-1]["stage"] == "failed"
    assert "could not be completed" in events[-1]["label"]


def test_counts_in_progress_detail_are_not_mangled():
    from app.progress import plural

    assert plural(1, "gap") == "1 gap"
    assert plural(0, "gap") == "0 gaps"
    assert plural(3, "statement") == "3 statements"


# --- the evidence rail -------------------------------------------------------


def test_the_source_fragment_is_a_fragment_not_a_page(client):
    """The rail loads this beside the draft, so it must not be a whole page."""
    http, _ = client
    thread_id = start_run(http)
    claim = http.get(f"/runs/{thread_id}/state.json").json()["claims"][0]

    body = http.get(
        f"/runs/{thread_id}/source/{claim['doc_id']}?claim={claim['claim_id']}"
    ).text

    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert "block-cited" in body, "the cited block is still marked"
    assert claim["claim_id"] in body


def test_the_full_page_and_the_fragment_agree(client):
    http, _ = client
    thread_id = start_run(http)
    claim = http.get(f"/runs/{thread_id}/state.json").json()["claims"][0]
    query = f"{claim['doc_id']}?claim={claim['claim_id']}"

    fragment = http.get(f"/runs/{thread_id}/source/{query}").text
    page = http.get(f"/runs/{thread_id}/evidence/{query}").text

    assert fragment.strip() in page, "the page embeds the same fragment"


def test_citation_chips_are_real_links_so_they_work_without_javascript(client):
    http, _ = client
    thread_id = start_run(http)
    body = http.get(f"/runs/{thread_id}").text

    assert f'href="/runs/{thread_id}/evidence/' in body
    assert 'data-cite=' in body and 'data-doc=' in body


def test_an_unknown_document_fragment_is_a_404(client):
    http, _ = client
    thread_id = start_run(http)
    assert http.get(f"/runs/{thread_id}/source/E99").status_code == 404
