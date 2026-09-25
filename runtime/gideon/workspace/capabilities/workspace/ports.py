"""Owned loopback port reservations with bounded conflict inspection."""
import errno
import fcntl
import json
import os
import socket
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .store import ConflictError


def now():
    return datetime.now(timezone.utc).isoformat()


class PortRegistry:
    def __init__(self, root, *, allowed_ports=range(6000, 6100)):
        self.allowed_ports = sorted(set(allowed_ports))
        if not self.allowed_ports or len(self.allowed_ports) > 256 or any(type(p) is not int or not 1024 <= p <= 65535 for p in self.allowed_ports):
            raise ValueError('Configure between 1 and 256 unprivileged ports')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / 'ports.lock').open('a')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise ConflictError('Another port registry owns this home') from None
        self.path = self.root / 'ports.sqlite3'
        self.sockets = {}
        self.closed = False
        self.mutex = threading.RLock()
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT NOT NULL, payload TEXT NOT NULL)')
            for record_id, payload in db.execute('SELECT id,payload FROM reservations').fetchall():
                record = json.loads(payload)
                if record['status'] == 'held':
                    record.update(status='interrupted', revision=record['revision'] + 1, released_at=now())
                    db.execute('UPDATE reservations SET payload=? WHERE id=?', (json.dumps(record), record_id))
        os.chmod(self.path, 0o600)

    def _bind(self, port):
        handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            handle.bind(('127.0.0.1', port))
            return handle
        except OSError:
            handle.close()
            raise

    def reserve(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'project_id','request_id','port'}:
            raise ValueError('Expected project_id, request_id and optional port')
        for key in ('project_id','request_id'):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f'Invalid {key}')
        requested = payload.get('port')
        if requested is not None and (type(requested) is not int or requested not in self.allowed_ports):
            raise ValueError('Port is outside the configured allocation')
        encoded = json.dumps(payload, sort_keys=True)
        with self.mutex, sqlite3.connect(self.path) as db:
            if self.closed:
                raise ConflictError('Port registry is closed')
            previous = db.execute('SELECT input,payload FROM reservations WHERE request_id=?', (payload['request_id'],)).fetchone()
            if previous:
                if previous[0] != encoded:
                    raise ConflictError('Request ID already used for another reservation')
                return json.loads(previous[1])
            if db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0] >= 1000:
                raise ConflictError('Reservation history capacity reached')
            handle = None
            for port in [requested] if requested is not None else self.allowed_ports:
                try:
                    handle = self._bind(port)
                    break
                except OSError as error:
                    if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                        raise
            if handle is None:
                raise ConflictError('No requested port is available')
            record = {'id':uuid4().hex,'project_id':payload['project_id'],'port':port,'status':'held',
                      'revision':1,'created_at':now(),'released_at':None}
            try:
                db.execute('INSERT INTO reservations VALUES(?,?,?,?)', (record['id'], payload['request_id'], encoded, json.dumps(record)))
                db.commit()
                self.sockets[record['id']] = handle
            except BaseException:
                handle.close()
                raise
            return record

    def get(self, reservation_id):
        with sqlite3.connect(self.path) as db:
            row = db.execute('SELECT payload FROM reservations WHERE id=?', (reservation_id,)).fetchone()
        if row is None:
            raise FileNotFoundError('Port reservation not found')
        return json.loads(row[0])

    def list(self, *, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid reservation pagination')
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM reservations ORDER BY rowid DESC LIMIT ? OFFSET ?', (limit, offset))]

    def release(self, reservation_id, revision):
        with self.mutex:
            record = self.get(reservation_id)
            if record['status'] == 'released' and type(revision) is int and revision == record['revision'] - 1:
                return record
            if type(revision) is not int or revision != record['revision']:
                raise ConflictError('Reservation revision changed')
            if record['status'] != 'held':
                return record
            handle = self.sockets.get(reservation_id)
            if handle is None:
                raise ConflictError('Reservation is not owned by this registry')
            record.update(status='released', revision=revision + 1, released_at=now())
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE reservations SET payload=? WHERE id=?', (json.dumps(record), reservation_id))
            handle.close()
            del self.sockets[reservation_id]
            return record

    def inventory(self):
        with self.mutex:
            if self.closed:
                raise ConflictError('Port registry is closed')
            held = {self.get(key)['port']:key for key in self.sockets}
            result = []
            for port in self.allowed_ports:
                reservation_id = held.get(port)
                available = False
                if reservation_id is None:
                    try:
                        handle = self._bind(port)
                        handle.close()
                        available = True
                    except OSError as error:
                        if error.errno not in (errno.EADDRINUSE, errno.EACCES):
                            raise
                result.append({'port':port,'available':available,'reservation_id':reservation_id})
            return {'host':'127.0.0.1','transport':'tcp4','ports':result}

    def close(self):
        with self.mutex:
            if self.closed:
                return
            for key in list(self.sockets):
                self.release(key, self.get(key)['revision'])
            self.closed = True
            fcntl.flock(self.lock_file, fcntl.LOCK_UN)
            self.lock_file.close()


_registries = {}


def get_port_registry(root, *, allowed_ports=range(6000, 6100)):
    key = str(Path(root).resolve())
    registry = _registries.get(key)
    if registry is None or registry.closed:
        registry = PortRegistry(root, allowed_ports=allowed_ports)
        _registries[key] = registry
    elif registry.allowed_ports != sorted(set(allowed_ports)):
        raise ConflictError('Port allocation changed while registry is active')
    return registry


def close_port_registry(root):
    registry = _registries.pop(str(Path(root).resolve()), None)
    if registry is not None:
        registry.close()
