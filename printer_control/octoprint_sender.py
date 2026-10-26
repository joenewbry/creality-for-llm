"""Pinned OctoPrint 1.11.8 integration adjustment, virtual connections only."""
from octoprint.util.comm import MachineCom


class VirtualSender(MachineCom):
    def __init__(self, *args, **kwargs):
        if args or kwargs.get('port') != 'VIRTUAL':
            raise ValueError('explicit VIRTUAL connection required')
        super().__init__(**kwargs)

    def sendCommand(self, cmd, cmd_type=None, part_of_job=False, processed=False,
                    force=False, on_sent=None, tags=None):
        result = super().sendCommand(cmd, cmd_type=cmd_type, part_of_job=part_of_job,
            processed=processed, force=force, on_sent=on_sent, tags=tags)
        if (result and cmd == 'M114' and part_of_job and
                'trigger:record_position' in (tags or ()) and
                self.getState() in (self.STATE_PAUSING, self.STATE_CANCELLING)):
            # M400's ack may precede its on_sent callback. That callback queues
            # M114 after the reader already found no work. Wake queue transfer;
            # the normal send loop still waits for real acknowledgment credit.
            self._continue_sending()
        return result
