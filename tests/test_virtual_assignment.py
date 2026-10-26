"""Adapter admission checks construct no sender and open no serial device."""
from types import SimpleNamespace
import pytest
from octoprint.plugins.virtual_printer.virtual import VirtualPrinter
from printer_control.journal import RequestRejected
from printer_control.octoprint_virtual import OctoPrintVirtualBackend


@pytest.mark.parametrize('port,virtual_type', [('VIRTUAL',False),('PHYSICAL_NOT_OPENED',True),('PHYSICAL_NOT_OPENED',False)])
def test_virtual_backend_rejects_nonvirtual_sender_before_job_access(tmp_path,port,virtual_type):
    device=VirtualPrinter.__new__(VirtualPrinter) if virtual_type else object()
    sender=SimpleNamespace(_port=port,_serial=device)
    with pytest.raises(RequestRejected,match='virtual_printer_required'):
        OctoPrintVirtualBackend(sender,job_path=tmp_path/'missing-job',job_id='not-started')
