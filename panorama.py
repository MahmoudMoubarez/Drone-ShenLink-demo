"""Robot-side perspective projection. The station never receives the panorama."""
import math
import threading
import time

import cv2
import numpy as np


class Projector:
    def __init__(self, width=640, height=360, fov=90):
        if not 10 <= fov <= 120:
            raise ValueError('FOV must be within 10..120 degrees')
        x,y = np.meshgrid((np.arange(width,dtype=np.float32)+.5-width/2)/(width/2),
                          -(np.arange(height,dtype=np.float32)+.5-height/2)/(width/2))
        scale = math.tan(math.radians(fov)/2)
        self.rays = np.stack((x*scale,y*scale,np.ones_like(x)),axis=-1)
        self.rays /= np.linalg.norm(self.rays,axis=-1,keepdims=True)
        self.cached = None

    def maps(self, shape, yaw, pitch):
        if not math.isfinite(yaw) or not math.isfinite(pitch) or not -80 <= pitch <= 80:
            raise ValueError('Invalid camera orientation')
        key = (tuple(shape),yaw,pitch)
        if self.cached is not None and self.cached[0]==key:
            return self.cached[1]
        a,b = math.radians(yaw),math.radians(pitch)
        x,y,z = np.moveaxis(self.rays,-1,0)
        yp,zp = y*math.cos(b)+z*math.sin(b), -y*math.sin(b)+z*math.cos(b)
        xp,zp = x*math.cos(a)+zp*math.sin(a), -x*math.sin(a)+zp*math.cos(a)
        height,width = shape[:2]
        # Horizontal panorama seam wraps, poles clamp; padding avoids seam artifacts.
        u = ((np.arctan2(xp,zp)/(2*math.pi)+.5)*width-.5)%width+1
        v = np.clip((.5-np.arcsin(np.clip(yp,-1,1))/math.pi)*height-.5,0,height-1)
        result = u.astype(np.float32),v.astype(np.float32)
        self.cached = key,result
        return result

    def render(self, image, yaw, pitch):
        maps = self.maps(image.shape,yaw,pitch)
        padded = np.concatenate((image[:,-1:],image,image[:,:1]),axis=1)
        return cv2.remap(padded,*maps,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)


class PanoramaSource:
    width,height,fps = 640,360,25

    def __init__(self, filename, snapshot):
        cv2.setNumThreads(1)
        self.filename,self.snapshot = str(filename),snapshot
        self.projector = Projector(self.width,self.height)
        self.capture = cv2.VideoCapture(self.filename)
        if not self.capture.isOpened():
            raise ValueError('Cannot open panorama video')
        self.input_fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.total = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.input_fps <= 0 or self.total <= 0:
            self.capture.release()
            raise ValueError('Cannot establish source timeline')
        self.index = 0
        self.stopped = threading.Event()
        self.worker = None
        self.error = None
        self.produced = 0

    def build_elements(self, pipeline):
        import gi
        gi.require_version('Gst','1.0')
        from gi.repository import Gst
        appsrc = Gst.ElementFactory.make('appsrc','panorama-source')
        appsrc.set_property('is-live',True)
        appsrc.set_property('format',Gst.Format.TIME)
        appsrc.set_property('block',False)
        appsrc.set_property('max-buffers',2)
        appsrc.set_property('leaky-type',2)
        appsrc.set_property('caps',Gst.Caps.from_string(
            f'video/x-raw,format=RGB,width={self.width},height={self.height},framerate={self.fps}/1'))
        pipeline.add(appsrc)
        def produce():
            start = time.monotonic()
            last_input = -1
            try:
                while not self.stopped.is_set():
                    tick = max(self.index,int((time.monotonic()-start)*self.fps))
                    target = int(tick*self.input_fps/self.fps)%self.total
                    if target < last_input:
                        self.capture.set(cv2.CAP_PROP_POS_FRAMES,0)
                        last_input = -1
                    for _ in range(target-last_input):
                        if not self.capture.grab():
                            raise RuntimeError('Panorama decoding failed')
                    ok,image = self.capture.retrieve()
                    if not ok:
                        raise RuntimeError('Panorama frame unavailable')
                    last_input = target
                    model = self.snapshot()
                    view = self.projector.render(image,model['yaw'],model['pitch'])
                    # Test witness encoded inside actual video, not a parallel UI value.
                    for bit in range(8):
                        view[0:20,bit*20:(bit+1)*20] = 255 if model['command_id'] & (1<<bit) else 0
                    rgb = cv2.cvtColor(view,cv2.COLOR_BGR2RGB).tobytes()
                    buffer = Gst.Buffer.new_allocate(None,len(rgb),None)
                    buffer.fill(0,rgb)
                    buffer.pts = buffer.dts = tick*Gst.SECOND//self.fps
                    buffer.duration = Gst.SECOND//self.fps
                    appsrc.emit('push-buffer',buffer)
                    self.produced += 1
                    self.index = tick+1
                    self.stopped.wait(max(0,start+self.index/self.fps-time.monotonic()))
            except Exception as exc:
                self.error = repr(exc)
        self.worker = threading.Thread(target=produce,name='robot-panorama',daemon=True)
        self.worker.start()
        return appsrc,appsrc

    def close(self):
        self.stopped.set()
        if self.worker:
            self.worker.join(10)
            if self.worker.is_alive():
                raise RuntimeError('Panorama worker failed to stop')
        self.capture.release()
