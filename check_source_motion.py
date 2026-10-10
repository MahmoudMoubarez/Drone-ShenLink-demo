"""Read-only check: does the recorded panorama change without camera commands?"""
import json
from pathlib import Path
import cv2
import numpy as np

path = Path('/home/edgelif2/work/viewer-360/insta360-corrected-square-pixels.mp4')
capture = cv2.VideoCapture(str(path))
try:
    fps = capture.get(cv2.CAP_PROP_FPS)
    count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    samples = []
    for second in (0,1,3,5):
        capture.set(cv2.CAP_PROP_POS_MSEC,second*1000)
        ok, frame = capture.read()
        if not ok:
            raise RuntimeError(f'Cannot decode source at {second}s')
        samples.append(cv2.resize(frame,(320,160)).astype(np.float32))
    print(json.dumps({'source_fps':fps,'duration_seconds':count/fps,
        'sample_seconds':[0,1,3,5],
        'mean_pixel_change_from_start':[float(np.abs(image-samples[0]).mean()) for image in samples[1:]],
        'scope':'Source content only; does not verify browser playback.'}))
finally:
    capture.release()
