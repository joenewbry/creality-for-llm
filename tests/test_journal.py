from concurrent.futures import ThreadPoolExecutor
import pytest
from printer_control.journal import Journal, RequestRejected

REQUEST = dict(request_id="request-1", printer_id="development", connection_generation="g1",
               expected_state_version=1, deadline=100, operation="printer.jog", arguments={"z_mm": 1})
CONTEXT = dict(now=1, printer_id="development", generation="g1", state_version=1)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "receipts.sqlite")


def test_identical_retry_returns_same_receipt_even_after_deadline(journal):
    first = journal.accept(REQUEST, **CONTEXT)
    assert journal.accept(REQUEST, **{**CONTEXT, "now": 200}) == first
    assert len(journal.events()) == 1
    with pytest.raises(RequestRejected):
        journal.accept({**REQUEST, "arguments": {"z_mm": 2}}, **CONTEXT)


def test_concurrent_agents_only_claim_once(journal):
    def submit(_):
        receipt = journal.accept(REQUEST, **CONTEXT)
        return journal.claim(receipt["operation_id"], now=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(submit, range(20)))
    assert sum(claims) == 1
    assert [e["status"] for e in journal.events()] == ["accepted", "running"]


@pytest.mark.parametrize("change,reason", [
    ({"printer_id": "production"}, "wrong_printer"),
    ({"connection_generation": "old"}, "stale_connection"),
    ({"deadline": 0}, "deadline_expired"),
    ({"expected_state_version": 0}, "stale_state"),
])
def test_invalid_context_creates_rejection_not_dispatch(journal, change, reason):
    receipt = journal.accept({**REQUEST, **change}, **CONTEXT)
    assert receipt["status"] == "rejected" and receipt["result"]["reason"] == reason
    assert journal.claim(receipt["operation_id"], now=2) is False


@pytest.mark.parametrize("change", [{"deadline": float('nan')}, {"arguments": {"z_mm": float('inf')}},
                                     {"expected_state_version": True}, {"extra": 1}, {"arguments": []}])
def test_invalid_json_or_schema_is_rejected(journal, change):
    with pytest.raises(RequestRejected):
        journal.accept({**REQUEST, **change}, **CONTEXT)
    assert journal.events() == []


def test_crash_after_dispatch_never_replays_or_claims_completion(journal):
    receipt = journal.accept(REQUEST, **CONTEXT)
    assert journal.claim(receipt["operation_id"], now=2)
    restarted = Journal(journal.path)
    assert restarted.recover_after_restart(now=3) == 1
    result = restarted.accept(REQUEST, **CONTEXT)
    assert result["status"] == "outcome_unknown"
    assert restarted.claim(receipt["operation_id"], now=4) is False
    with pytest.raises(RequestRejected):
        restarted.finish(receipt["operation_id"], "completed", {}, now=5)


def test_deadline_is_rechecked_when_dispatch_is_delayed(journal):
    receipt = journal.accept(REQUEST, **CONTEXT)
    assert journal.claim(receipt["operation_id"], now=100) is False
    assert journal.get(receipt["operation_id"])["status"] == "rejected"


def test_completion_requires_dispatch_and_is_immutable(journal):
    receipt = journal.accept(REQUEST, **CONTEXT)
    op = receipt["operation_id"]
    with pytest.raises(RequestRejected):
        journal.finish(op, "completed", {}, now=2)
    assert journal.claim(op, now=3)
    completed = journal.finish(op, "completed", {"evidence": "simulator motion drained"}, now=4)
    assert journal.finish(op, "completed", completed["result"], now=5) == completed
    assert len(journal.events()) == 3


@pytest.mark.parametrize("now", [float('nan'), float('inf'), True, "tomorrow"])
def test_invalid_clock_cannot_dispatch_or_recover(journal, now):
    receipt = journal.accept(REQUEST, **CONTEXT)
    with pytest.raises(RequestRejected):
        journal.claim(receipt["operation_id"], now=now)
    with pytest.raises(RequestRejected):
        journal.recover_after_restart(now=now)
    assert journal.get(receipt["operation_id"])["status"] == "accepted"
