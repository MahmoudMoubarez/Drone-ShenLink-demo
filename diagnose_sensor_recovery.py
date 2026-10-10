"""Read-only recovery timing experiment with the pinned message engine.

No sockets/netem, no SDK edits: fake paths model HELLO loss and 60 ms probe RTT.
This reproduces a mechanism, not the missing packet trace of a historical run.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time


def experiment(paths, restore_after_probe):
    from multiflow_comm.messages import MessageSender
    from multiflow_comm.transport import FakeMessageSenderAdapter
    from multiflow_comm.types import ConnectionState, DeliveryMode, ReceiptPolicy
    from multiflow_comm.wire import decode_frame, encode_frame

    restored, recovered, anchor = threading.Event(), threading.Event(), threading.Event()
    probes, timers = [], []
    recovery_ns = [None]
    start_ns = time.monotonic_ns()

    class ProbePath(FakeMessageSenderAdapter):
        def send_probe(self, frame):
            header, _ = decode_frame(frame)
            probes.append(dict(path=self.path_id, at_ms=(time.monotonic_ns()-start_ns)/1e6,
                               restored=restored.is_set()))
            if self.path_id == restore_after_probe:
                anchor.set()
            if self.path_id == 'c' and restored.is_set():
                ack = encode_frame(kind='ACK', session_id=header['session_id'],
                    channel_id=header['channel_id'], message_id=header['message_id'],
                    sequence=0, mode=header['mode'], priority=header['priority'],
                    receipt='NONE', payload=b'')
                timer = threading.Timer(.06, lambda: sender._on_inbound_ack('c', ack))
                timers.append(timer)
                timer.start()
            return True  # UDP locally accepts sends even when the path drops them.

        def record_success(self):
            self.state = ConnectionState.CONNECTED
            if self.path_id == 'c' and not recovered.is_set():
                recovery_ns[0] = time.monotonic_ns()
                recovered.set()

        def record_timeout(self):
            self.state = ConnectionState.FAILED

    adapters = [ProbePath(path) for path in paths]
    for adapter in adapters:
        adapter.state = ConnectionState.FAILED
    sender = MessageSender('sensors', adapters, mode=DeliveryMode.LATEST_ONLY,
                           receipt=ReceiptPolicy.NONE, probe_interval_s=.2)
    try:
        if restore_after_probe is None:
            restored_ns = time.monotonic_ns()
            restored.set()
        sender._probe_thread = threading.Thread(target=sender._probe_loop, daemon=True)
        sender._probe_thread.start()
        if restore_after_probe is not None:
            assert anchor.wait(3), 'Probe did not begin'
            time.sleep(.05)
            restored_ns = time.monotonic_ns()
            restored.set()
        assert recovered.wait(5), 'Satellite did not recover'
        return dict(paths=paths, restore_after_probe=restore_after_probe,
                    probe_timeout_ms=sender.probe_timeout_s*1000,
                    restore_to_verified_ms=(recovery_ns[0]-restored_ns)/1e6,
                    probes=list(probes))
    finally:
        sender.close()
        for timer in timers:
            timer.join(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.release.resolve()/'src'))
    cases = [experiment(['c'], None), experiment(['a','b','c'], 'a'),
             experiment(['a','b','c'], 'c')]
    assert cases[0]['restore_to_verified_ms'] < 500
    assert cases[1]['restore_to_verified_ms'] < 850
    assert cases[2]['restore_to_verified_ms'] < 850
    print(json.dumps(dict(status='PASS', scope='Actual SDK probe loop; fake paths only; no production changes',
                          cases=cases), indent=2))


if __name__ == '__main__':
    main()
