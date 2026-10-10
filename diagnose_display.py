"""Read-only timing check of an existing viewer and source metadata."""
import json
import time
from urllib.request import urlopen
import cv2

base='http://127.0.0.1:8791'
report={}
capture=cv2.VideoCapture('/home/edgelif2/work/viewer-360/insta360-corrected-square-pixels.mp4')
report['panorama']={'width':capture.get(cv2.CAP_PROP_FRAME_WIDTH),
                    'height':capture.get(cv2.CAP_PROP_FRAME_HEIGHT),
                    'fps':capture.get(cv2.CAP_PROP_FPS)}
capture.release()
try:
    def state():
        with urlopen(base+'/state',timeout=3) as response:
            return json.load(response)
    first=state();started=time.monotonic();time.sleep(4);last=state()
    report['sdk_receive_fps']=(last['frames']-first['frames'])/(time.monotonic()-started)
    durations=[]
    for _ in range(8):
        started=time.monotonic()
        with urlopen(base+'/frame',timeout=3) as response:
            response.read()
        durations.append((time.monotonic()-started)*1000)
    report['local_jpeg_request_ms_mean']=sum(durations)/len(durations)
    report['stale']=last['stale']
    report['errors']=last['errors']
except OSError as error:
    report['existing_viewer_unavailable']=str(error)
print(json.dumps(report))
