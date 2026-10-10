"""Local test orchestration only. All application traffic still uses the SDK.

Files cross namespace boundaries to trigger station commands and collect evidence;
they never render the camera, execute robot commands, or carry robot telemetry.
"""
import json
from pathlib import Path
import time


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def gate_session(sender, snapshot, seconds, stopped, directory):
    directory = Path(directory)
    deadline = time.monotonic()+seconds
    processed = 0
    checks = []
    tickets = []
    request = directory.parent/'control.json'
    while time.monotonic()<deadline and not stopped.is_set():
        if request.exists():
            value = json.loads(request.read_text())
            index = value['id']
            if index>processed:
                if index != processed+1:
                    raise ValueError('Gate command sequence skipped')
                # Test poses have an expiry too: never act on old queued commands.
                ticket = sender.send_object(dict(value, expires_ns=time.monotonic_ns()+300_000_000))
                tickets.append((index,ticket))
                checks.append({'id':index,'sent_ns':time.monotonic_ns()})
                processed = index
        state = snapshot()
        state.update(sent=processed, tickets={str(i):ticket.status.value for i,ticket in tickets})
        atomic_json(directory/'station-state.json',state)
        if state['errors']:
            raise RuntimeError(str(state['errors']))
        stopped.wait(.1)
    return checks
