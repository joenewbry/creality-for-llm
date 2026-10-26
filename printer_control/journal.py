"""Durable command receipts. A journal entry is not proof of physical execution."""
import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class RequestRejected(ValueError):
    pass


class Journal:
    REQUIRED = {"request_id", "printer_id", "connection_generation", "expected_state_version", "deadline", "operation", "arguments"}
    TERMINAL = {"completed", "rejected", "failed", "outcome_unknown"}

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS operations (
                    request_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
                    fingerprint TEXT NOT NULL, request TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at REAL NOT NULL, source TEXT NOT NULL, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL, status TEXT NOT NULL,
                    observed_at REAL NOT NULL, detail TEXT NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _time(now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise RequestRejected("Invalid current time")

    @staticmethod
    def _encode(value):
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError, RecursionError) as exc:
            raise RequestRejected("Request must contain finite JSON values") from exc
        if len(encoded.encode()) > 131072:
            raise RequestRejected("Request exceeds 128 KiB")
        return encoded

    @classmethod
    def validate(cls, request):
        if not isinstance(request, dict) or request.keys() != cls.REQUIRED:
            raise RequestRejected("Missing or unknown request fields")
        for key in ("request_id", "printer_id", "connection_generation", "operation"):
            if type(request[key]) is not str or not request[key].strip() or len(request[key]) > 128:
                raise RequestRejected(f"Invalid {key}")
        if type(request["expected_state_version"]) is not int or request["expected_state_version"] < 0:
            raise RequestRejected("Invalid expected_state_version")
        if type(request["deadline"]) not in (float, int) or not math.isfinite(request["deadline"]):
            raise RequestRejected("Invalid deadline")
        if type(request["arguments"]) is not dict:
            raise RequestRejected("arguments must be an object")
        return cls._encode(request)

    @staticmethod
    def _receipt(row):
        return {"request_id": row["request_id"], "operation_id": row["operation_id"],
                "status": row["status"], "result": json.loads(row["result"])}

    @staticmethod
    def _event(db, operation_id, status, now, result):
        db.execute("INSERT INTO events(operation_id,status,observed_at,detail) VALUES (?,?,?,?)",
                   (operation_id, status, now, result))

    def accept(self, request: dict, *, now: float, printer_id: str, generation: str, state_version: int):
        encoded = self.validate(request)
        self._time(now)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM operations WHERE request_id=?", (request["request_id"],)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise RequestRejected("Request ID reused with different content")
                return self._receipt(existing)
            reason = None
            if request["printer_id"] != printer_id:
                reason = "wrong_printer"
            elif request["connection_generation"] != generation:
                reason = "stale_connection"
            elif request["deadline"] <= now:
                reason = "deadline_expired"
            elif request["expected_state_version"] != state_version:
                reason = "stale_state"
            status = "rejected" if reason else "accepted"
            result = self._encode({"reason": reason} if reason else {})
            op_id = uuid.uuid4().hex
            db.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)",
                       (request["request_id"], op_id, fingerprint, encoded, status, result))
            self._event(db, op_id, status, now, result)
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (op_id,)).fetchone()
            return self._receipt(row)

    def claim(self, operation_id: str, *, now: float) -> bool:
        """Persist 'running' BEFORE any device write; only one caller wins.

        The controller must revalidate physical state/capabilities under its own
        exclusive state lock before calling this method and issuing a command.
        """
        self._time(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row or row["status"] != "accepted":
                return False
            if json.loads(row["request"])["deadline"] <= now:
                result = self._encode({"reason": "deadline_expired_before_dispatch"})
                db.execute("UPDATE operations SET status='rejected',result=? WHERE operation_id=?", (result, operation_id))
                self._event(db, operation_id, "rejected", now, result)
                return False
            db.execute("UPDATE operations SET status='running' WHERE operation_id=?", (operation_id,))
            self._event(db, operation_id, "running", now, "{}")
            return True

    def finish(self, operation_id: str, status: str, result: dict, *, now: float):
        self._time(now)
        if status not in self.TERMINAL:
            raise RequestRejected("Invalid terminal status")
        encoded = self._encode(result)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row:
                raise RequestRejected("Unknown operation")
            if row["status"] in self.TERMINAL:
                if row["status"] != status or row["result"] != encoded:
                    raise RequestRejected("Cannot rewrite terminal outcome")
                return self._receipt(row)
            if status == "completed" and row["status"] != "running":
                raise RequestRejected("Operation has not been dispatched")
            db.execute("UPDATE operations SET status=?,result=? WHERE operation_id=?", (status, encoded, operation_id))
            self._event(db, operation_id, status, now, encoded)
            return self._receipt(db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone())

    def recover_after_restart(self, *, now: float) -> int:
        """Only the exclusive service owner may call this at startup. Never replays."""
        self._time(now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT operation_id FROM operations WHERE status IN ('accepted','running')").fetchall()
            result = self._encode({"reason": "host_restarted", "requires_reconciliation": True})
            for row in rows:
                db.execute("UPDATE operations SET status='outcome_unknown',result=? WHERE operation_id=?", (result, row[0]))
                self._event(db, row[0], "outcome_unknown", now, result)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thermal_sessions'").fetchone():
                db.execute("UPDATE thermal_sessions SET state='invalid',snapshot=? WHERE state NOT IN ('consumed','invalid')", (result,))
                db.execute("UPDATE thermal_effects SET status='outcome_unknown',result=? WHERE status='dispatching'", (result,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='restore_effects'").fetchone():
                db.execute("UPDATE restore_effects SET invalidated=COALESCE(invalidated,'host_restarted'),status=CASE WHEN status IN ('dispatching','resume_dispatching') THEN 'outcome_unknown' ELSE status END WHERE status != 'resumed'")
            return len(rows)

    def has_unreconciled_restore(self):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='restore_effects'").fetchone():
                return False
            return db.execute("SELECT 1 FROM restore_effects WHERE status != 'resumed'").fetchone() is not None

    def has_unreconciled_thermal_pause(self):
        with self._db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thermal_sessions'").fetchone():
                return False
            return db.execute("SELECT 1 FROM thermal_sessions WHERE state != 'consumed'").fetchone() is not None

    def get(self, operation_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row:
                raise RequestRejected("Unknown operation")
            return self._receipt(row)

    def events(self, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RequestRejected("Invalid event bounds")
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit))]


    def record_observation(self, snapshot, *, source, now):
        self._time(now)
        if type(snapshot) is not dict or source not in ('printer.status', 'job.inspect', 'job.pause_status', 'calibration.inspect'):
            raise RequestRejected('Invalid state observation')
        encoded = self._encode(snapshot)
        with self._db() as db:
            cursor = db.execute('INSERT INTO observations(recorded_at,source,snapshot) VALUES (?,?,?)',
                                (now, source, encoded))
            return cursor.lastrowid

    def latest_pause_evidence(self):
        """Bounded summaries of durable history; never restored live authority."""
        result = {'thermal':None,'restoration':None}
        with self._db() as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'thermal_sessions' in tables:
                row=db.execute('SELECT * FROM thermal_sessions ORDER BY rowid DESC LIMIT 1').fetchone()
                if row:
                    snapshot=json.loads(row['snapshot'])
                    effects=[]
                    for effect in db.execute('SELECT effect_id,status,action FROM thermal_effects WHERE checkpoint_id=? ORDER BY rowid LIMIT 2',(row['checkpoint_id'],)):
                        action=json.loads(effect['action'])
                        effects.append(dict(effect_id=effect['effect_id'],status=effect['status'],
                            nozzle_target=action['nozzle_target'],bed_target=action['bed_target']))
                    result['thermal']=dict(checkpoint_id=row['checkpoint_id'],state=row['state'],
                        reason=snapshot.get('reason'),effects=effects)
            if 'restore_effects' in tables:
                row=db.execute('SELECT * FROM restore_effects ORDER BY rowid DESC LIMIT 1').fetchone()
                if row:
                    receipt=json.loads(row['result'])
                    result['restoration']=dict(plan_id=row['plan_id'],checkpoint_id=row['checkpoint_id'],
                        state=row['status'],invalidated=row['invalidated'],
                        resume_operation_id=receipt.get('resume_operation_id'))
        return result

    def observations(self, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RequestRejected('Invalid observation bounds')
        # Bound response bytes too: a single observation is limited to 128 KiB.
        # Sequence cursors allow callers to retrieve the rest without omissions.
        rows, size = [], 0
        with self._db() as db:
            for row in db.execute('SELECT * FROM observations WHERE sequence>? ORDER BY sequence LIMIT ?', (after, limit)):
                encoded_size = len(row['snapshot'].encode())
                if rows and size + encoded_size > 262144:
                    break
                rows.append({**dict(row), 'snapshot': json.loads(row['snapshot'])})
                size += encoded_size
        return rows
