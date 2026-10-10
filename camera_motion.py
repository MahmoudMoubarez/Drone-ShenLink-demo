"""Application camera model; timestamps share one host clock in this demo only."""
import math
import time


class CameraMotion:
    def __init__(self, clock=time.monotonic_ns):
        self.clock=clock
        self.state={'yaw':0.0,'pitch':0.0,'command_id':0,'executed_ns':None,'executions':0,
                    'expired_commands':0}
        self.last_id=0
        self.last_ns=clock()
        self.until_ns=self.last_ns
        self.yaw_rate=self.pitch_rate=0.0
        self.target=None

    def advance(self,now):
        duration=max(0,min(now,self.until_ns)-self.last_ns)/1e9
        if self.target is not None:
            # Robot-side integration, never a locally animated camera preview.
            while duration>1e-9:
                dt=min(duration,.005)
                for axis,index in (('yaw',0),('pitch',1)):
                    distance=self.target[index]-self.state[axis]
                    if axis=='yaw':
                        distance=(distance+180)%360-180
                    rate=getattr(self,axis+'_rate')
                    desired=max(-90,min(90,6*distance))
                    rate+=max(-360*dt,min(360*dt,desired-rate))
                    step=rate*dt
                    if distance*step>=0 and abs(step)>=abs(distance):
                        step=distance
                        rate=0.0
                    self.state[axis]+=step
                    self.state[axis]=(self.state[axis]+180)%360-180 if axis=='yaw' else max(-75,min(75,self.state[axis]))
                    setattr(self,axis+'_rate',rate)
                duration-=dt
            if now>=self.until_ns:
                self.target=None
                self.yaw_rate=self.pitch_rate=0.0
            self.last_ns=now
            return
        self.state['yaw']=(self.state['yaw']+self.yaw_rate*duration+180)%360-180
        self.state['pitch']=max(-75,min(75,self.state['pitch']+self.pitch_rate*duration))
        self.last_ns=now

    def apply(self,value):
        now=self.clock()
        self.advance(now)
        if value['id']!=self.last_id+1:
            raise ValueError('Command duplicate or out of order')
        self.last_id=value['id']
        expires=value.get('expires_ns')
        if expires is not None and expires<=now:
            self.state['expired_commands']+=1
            return
        mode=value.get('mode','absolute')
        fields={'absolute':('yaw','pitch',180), 'delta':('yaw_delta','pitch_delta',180),
                'target':('yaw','pitch',180),
                'velocity':('yaw_rate','pitch_rate',25)}
        if mode not in fields:
            raise ValueError('Invalid camera command mode')
        a,b,limit=fields[mode]
        x,y=value[a],value[b]
        if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>limit for v in (x,y)):
            raise ValueError('Invalid camera command values')
        if mode in ('velocity','target'):
            if expires is None or not now<expires<=now+300_000_000:
                raise ValueError('Motion needs a bounded, same-host command lease')
            if mode=='target':
                self.target=((x+180)%360-180,max(-75,min(75,y)))
            else:
                self.target=None
                self.yaw_rate,self.pitch_rate=x,y
            self.until_ns=expires
        else:
            self.target=None
            self.yaw_rate=self.pitch_rate=0.0
            self.until_ns=now
            if mode=='absolute':
                self.state['yaw']=(x+180)%360-180
                self.state['pitch']=max(-75,min(75,y))
            else:
                self.state['yaw']=(self.state['yaw']+x+180)%360-180
                self.state['pitch']=max(-75,min(75,self.state['pitch']+y))
        self.state.update(command_id=value['id'],executed_ns=now,executions=self.state['executions']+1)

    def snapshot(self):
        self.advance(self.clock())
        return dict(self.state)
