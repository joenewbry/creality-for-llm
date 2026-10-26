"""Assigned Linux descriptor -> one frame writer -> journaled prompt backend.

Prompt-only, uncommissioned transport integration. No default launcher exposes
this factory. Use it inside OperationService.from_backend_factory only after
independent readiness/assignment qualification. Job sender integration is separate.
"""
import uuid
from .prompt_backend import PromptBackend
from .prompt_channel import PromptChannel
from .shared_writer import SharedFrameWriter
from .tty_transport import open_assigned_transport


class TTYPromptBackend(PromptBackend):
    @classmethod
    def open(cls, assignment, discover, now, is_busy, lease, *, max_age=5.0, io_timeout=.05):
        printer_id=assignment.printer_id
        if type(printer_id) is not str or not 1<=len(printer_id)<=128 or not printer_id.strip():
            raise ValueError('bounded explicit printer identity required')
        transport=None
        def write(data):
            if transport is None: raise OSError('assigned transport not constructed')
            return transport.write(data)
        # Validate protocol options before any device open. A fresh host-side
        # generation never turns a prior firmware session into resume authority.
        writer=SharedFrameWriter(write)
        channel=PromptChannel(writer.write_prompt,uuid.uuid4().hex,max_age=max_age)
        transport=open_assigned_transport(assignment,discover,now,is_busy,lease,io_timeout=io_timeout)
        try:
            def pump():
                try: return transport.read()
                except Exception:
                    # Assignment/lease loss may be ValueError/RuntimeError rather
                    # than OSError. Preserve uncertainty through the prompt lane.
                    raise OSError('assigned_prompt_receive_unavailable') from None
            backend=cls(channel,printer_id=printer_id,clock=now,pump=pump,close_transport=transport.close)
            backend.transport=transport
            backend.frame_writer=writer
            return backend
        except BaseException:
            transport.close()
            raise

    def capabilities(self):
        return {**super().capabilities(),'transport_kind':'assigned_linux_tty',
                'connection_scope':'prompt_protocol_only','commissioning_status':'unqualified',
                'automatic_reconnect_supported':False,'physical_control_qualified':False}

    def inspect(self):
        return {**super().inspect(),'transport_fault':self.transport.fault}
