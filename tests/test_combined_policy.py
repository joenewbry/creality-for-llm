"""The prompt lane is a narrow exception to ordinary operation serialization."""
import pytest
from printer_control.combined_virtual import CombinedVirtualBackend


@pytest.mark.parametrize('pending', ['job.start', 'job.pause', 'job.resume'])
@pytest.mark.parametrize('new', ['prompt.inspect', 'prompt.bind', 'prompt.respond'])
def test_only_prompt_lane_can_overlap_a_waiting_job_transition(pending, new):
    backend = CombinedVirtualBackend.__new__(CombinedVirtualBackend)
    from printer_control.prompt_backend import PromptBackend
    backend.prompt = PromptBackend.__new__(PromptBackend)
    backend._routes = {'pending': pending}
    assert backend.can_overlap(new, ('pending',))
    assert not backend.can_overlap('job.pause', ('pending',))


@pytest.mark.parametrize('pending', ['job.stage', 'job.cancel', 'prompt.inspect', 'prompt.bind', 'prompt.respond', 'unknown'])
def test_prompt_lane_cannot_overlap_another_prompt_or_shutdown(pending):
    backend = CombinedVirtualBackend.__new__(CombinedVirtualBackend)
    from printer_control.prompt_backend import PromptBackend
    backend.prompt = PromptBackend.__new__(PromptBackend)
    backend._routes = {'pending': pending}
    assert not backend.can_overlap('prompt.respond', ('pending',))
    assert not backend.can_overlap('prompt.respond', ('missing',))
