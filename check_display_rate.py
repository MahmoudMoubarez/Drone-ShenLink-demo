"""Measure endpoint and preview rates; not browser paint or pure SDK latency."""
import json
import sys
import time
from urllib.request import urlopen

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8792'
def state():
    with urlopen(base+'/state',timeout=5) as response:
        return json.load(response)
first=state();start=time.monotonic();time.sleep(8);last=state()
elapsed=time.monotonic()-start
report={'elapsed_seconds':elapsed,
        'sdk_frames_per_second':(last['frames']-first['frames'])/elapsed,
        'preview_encoder_fps':last['preview_encode_fps'],
        'stale':last['stale'],'errors':last['errors'],
        'scope':'Application pipeline only. Not measured browser paint or isolated library cost.'}
print(json.dumps(report),flush=True)
assert not last['stale'] and not last['errors'] and last['frames']>first['frames'],report
