import pytest
from printer_control.journal import Journal, RequestRejected


def test_order_pagination_restart_and_source_timestamps(tmp_path):
    path = tmp_path / 'events.sqlite'
    journal = Journal(path)
    states = [{'observed_at': 9, 'job_state': 'paused'}, {'observed_at': 8, 'job_state': 'faulted'}]
    for state in states:
        journal.record_observation(state, source='printer.status', now=10)
    reopened = Journal(path)
    first = reopened.observations(limit=1)
    second = reopened.observations(after=first[0]['sequence'])
    assert [e['snapshot'] for e in first + second] == states
    assert second[0]['sequence'] > first[0]['sequence']
    assert all(e['recorded_at'] == 10 for e in first + second)
    assert reopened.events() == []  # operation and observation cursors are separate


def test_observation_page_byte_bound_does_not_skip_rows(tmp_path):
    journal = Journal(tmp_path / 'events.sqlite')
    for i in range(4):
        journal.record_observation({'payload': 'x' * 100000, 'i': i}, source='job.inspect', now=i)
    first = journal.observations(limit=1000)
    second = journal.observations(after=first[-1]['sequence'], limit=1000)
    assert [e['snapshot']['i'] for e in first + second] == [0, 1, 2, 3]
    assert len(first) == len(second) == 2


@pytest.mark.parametrize('snapshot', [{'value': float('nan')}, {'value': 'x' * 131072}, []])
def test_invalid_observation_is_not_recorded(tmp_path, snapshot):
    journal = Journal(tmp_path / 'events.sqlite')
    with pytest.raises(RequestRejected):
        journal.record_observation(snapshot, source='printer.status', now=10)
    assert journal.observations() == []
