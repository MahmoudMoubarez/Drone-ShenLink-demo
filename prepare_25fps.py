"""Prepare a lighter source without changing dimensions or overwriting input."""
import argparse
from pathlib import Path
import gi

gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path)
parser.add_argument('output',type=Path)
args=parser.parse_args()
if not args.source.is_file() or args.output.exists():
    parser.error('Input must exist and output must be a new file')
pipeline=Gst.parse_launch('filesrc name=input ! decodebin ! videoconvert ! videorate ! '
    'video/x-raw,framerate=25/1 ! x264enc speed-preset=veryfast tune=zerolatency pass=qual quantizer=18 '
    '! h264parse ! mp4mux ! filesink name=output')
pipeline.get_by_name('input').set_property('location',str(args.source.resolve()))
pipeline.get_by_name('output').set_property('location',str(args.output.resolve()))
try:
    pipeline.set_state(Gst.State.PLAYING)
    message=pipeline.get_bus().timed_pop_filtered(600*Gst.SECOND,Gst.MessageType.ERROR|Gst.MessageType.EOS)
    if message is None:
        raise TimeoutError('Preparation timed out; output is incomplete')
    if message.type==Gst.MessageType.ERROR:
        raise RuntimeError(str(message.parse_error()))
    print('PASS: prepared 25 FPS panorama; original unchanged.',flush=True)
finally:
    pipeline.set_state(Gst.State.NULL)
