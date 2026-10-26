"""Bounded asynchronous serial observations, never command/recovery authority.

The caller must hold the controller lease throughout capture and close this
recorder before releasing it. Hook input is copied; sender replies are unchanged.
"""
from collections import deque
from dataclasses import asdict
import threading
import time
import uuid

from .journal import Journal, RequestRejected
from .protocol import ProtocolObserver


class MachineEventRecorder:
    def __init__(self, journal, *, generation, clock=time.time, retention=10000, capacity=262144):
        if type(retention) is not int or not 1 <= retention <= 100000:
            raise ValueError('bounded event retention required')
        if type(capacity) is not int or not 128 <= capacity <= 1048576:
            raise ValueError('bounded receive capacity required')
        if type(generation) is not str or not generation or len(generation) > 128:
            raise ValueError('connection generation required')
        self.journal, self.clock = journal, clock
        self.retention, self.capacity = retention, capacity
        self.capture_id = uuid.uuid4().hex
        self.observer = ProtocolObserver(generation)
        self._condition = threading.Condition()
        self._queue, self._size = deque(), 0
        self._closing, self._closed, self._fault = False, False, None
        self._last_received = self._last_persisted = None
        with journal._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS machine_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id TEXT NOT NULL, event TEXT NOT NULL)''')
        self._persist([dict(kind='capture_started', generation=generation,
                            observed_at=clock(), data={'source':'serial_receive_copy', 'recovery_authority':False})])
        self._worker = threading.Thread(target=self._run, name='printer-machine-events', daemon=False)
        self._worker.start()

    def _invalidate(self, reason):
        self._fault = self._fault or reason
        self._condition.notify_all()

    @property
    def fault(self):
        with self._condition:return self._fault

    @property
    def capturing(self):
        with self._condition:return not self._closing and not self._closed and self._fault is None

    def _enqueue(self,data,now,provenance=None):
        with self._condition:
            if self._closing or self._fault is not None:return False
            try:
                if type(data) is not bytes:raise ValueError('received bytes required')
                Journal._time(now)
            except Exception:
                self._invalidate('invalid_receive_observation')
                return False
            if len(data)>self.capacity-self._size or len(self._queue)>=4096:
                self._invalidate('receive_capture_overflow')
                return False
            if data:
                self._queue.append((data,now,provenance))
                self._size+=len(data);self._last_received=now
                self._condition.notify()
            return True

    def received_hook(self, sender, line, *args, **kwargs):
        with self._condition:
            if self._closing or self._fault is not None:return line
        try:
            if type(line) is not str:raise ValueError('invalid line')
            data,now=line.encode('utf-8'),self.clock()
        except Exception:
            with self._condition:self._invalidate('invalid_receive_observation')
        else:self._enqueue(data,now)
        return line

    def _persist(self, events):
        encoded = []
        for event in events:
            Journal._time(event['observed_at'])
            encoded.append(Journal._encode(event))
        if not encoded:
            return
        with self.journal._db() as db:
            db.executemany('INSERT INTO machine_events(capture_id,event) VALUES (?,?)',
                           [(self.capture_id, event) for event in encoded])
            # Retention and insertion are one transaction. AUTOINCREMENT preserves
            # cursor gaps across pruning, so a reader can detect lost history.
            db.execute('''DELETE FROM machine_events WHERE sequence <= (
                SELECT sequence FROM machine_events ORDER BY sequence DESC LIMIT 1 OFFSET ?
            )''', (self.retention,))
        with self._condition:
            self._last_persisted = events[-1]['observed_at']

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._closing or self._fault)
                    if self._fault:
                        break
                    if not self._queue and self._closing:
                        break
                    data, now, provenance = self._queue.popleft()
                    self._size -= len(data)
                generation=self.observer.generation
                events=[asdict(event) for event in self.observer.feed(data,now)]
                if provenance is not None:
                    for event in events:event['data']['receive_provenance']=dict(provenance)
                    events.insert(0,dict(kind='serial_line_received',generation=generation,observed_at=now,
                        data={'text':data.decode('ascii'),'receive_provenance':dict(provenance)}))
                self._persist(events)
            now = self.clock()
            final_events = self.observer.end_capture(now)
            with self._condition:
                if any(event.kind == 'protocol_error' and event.data.get('reason') == 'truncated_line'
                       for event in final_events):
                    self._invalidate('incomplete_serial_line')
                reason = self._fault
            self._persist([asdict(event) for event in final_events] + [dict(kind='capture_lost' if reason else 'capture_closed',
                generation=self.observer.generation, observed_at=now,
                data={'reason':reason, 'physical_state':'unknown', 'recovery_authority':False})])
        except Exception:
            with self._condition:
                self._invalidate('event_capture_failed')
        finally:
            with self._condition:
                self._queue.clear()
                self._size = 0
                self._closed = True
                self._condition.notify_all()

    def read(self, after=0, limit=100):
        import json
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RequestRejected('invalid_machine_event_bounds')
        with self.journal._db() as db:
            # Keep boundaries and page in one read snapshot under concurrent pruning.
            db.execute('BEGIN')
            first, last = db.execute('SELECT MIN(sequence),MAX(sequence) FROM machine_events').fetchone()
            rows, size = [], 0
            for row in db.execute('SELECT * FROM machine_events WHERE sequence>? ORDER BY sequence LIMIT ?', (after, limit)):
                length = len(row['event'].encode())
                if rows and size + length > 262144:
                    break
                rows.append(dict(sequence=row['sequence'], capture_id=row['capture_id'], event=json.loads(row['event'])))
                size += length
        with self._condition:
            health = dict(capture_id=self.capture_id, capturing=not self._closing and not self._closed and self._fault is None,
                          fault=self._fault, pending_bytes=self._size,
                          last_received_at=self._last_received, last_persisted_at=self._last_persisted)
        return dict(scope='serial_machine_observations', recovery_authority=False,
            generation_scope='protocol_observer', coverage='receive_hook_since_capture_started',
            retention_events=self.retention, retained_from=first, retained_through=last,
            history_gap=bool(first is not None and after < first - 1),
            cursor_ahead=after > (last or 0), events=rows,
            next_after=rows[-1]['sequence'] if rows else after, health=health)

    def close(self, timeout=2):
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._worker.join(timeout)
        if self._worker.is_alive():
            raise TimeoutError('machine event recorder still owns journal writes')
