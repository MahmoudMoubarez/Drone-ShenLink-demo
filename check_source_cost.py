"""Offline diagnostic: source decoding and projection, without SDK or HTTP."""
import json
import sys
import time
from pathlib import Path
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parent))
from panorama import Projector

cv2.setNumThreads(1)
for filename in sys.argv[1:]:
    capture = cv2.VideoCapture(filename)
    projector = Projector(640, 360)
    decode = render = 0.0
    count = 0
    shape = None
    try:
        for index in range(75):
            start = time.perf_counter()
            ok, image = capture.read()
            decoded = time.perf_counter()
            if not ok:
                raise RuntimeError('Source decoding failed')
            projector.render(image, index * .5, 0)
            end = time.perf_counter()
            if index >= 25:
                decode += decoded - start
                render += end - decoded
                count += 1
                shape = list(image.shape)
    finally:
        capture.release()
    print(json.dumps({'source': filename, 'shape': shape,
                      'sample_frames': count, 'decode_ms': decode/count*1000,
                      'projection_ms': render/count*1000,
                      'scope': 'Offline diagnostic, not streaming FPS or isolated library performance'}), flush=True)
