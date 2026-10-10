'use strict';
// No local camera animation, optimistic path switching, or simulated sensor values.
const token=document.querySelector('meta[name="shen-session"]').content;
const view=document.getElementById('viewport'),image=document.getElementById('image');
const keys=new Set(),keyMap={UP:'ArrowUp',DOWN:'ArrowDown',LEFT:'ArrowLeft',RIGHT:'ArrowRight'};
let dx=0,dy=0,dirty=false,centre=false,velocityDirty=false,commandBusy=false,drag=null;
let liveState=null,networkState=null,networkBusy=false,lastPath=null,networkError='',stateAt=0;
let linkMarkup='',autoActive=false,autoNext=0;
let lastAutoPath=null;
const byId=id=>document.getElementById(id);
const signed=value=>Number.isFinite(value)?`${value>=0?'+':''}${value.toFixed(1)}°`:'—';
async function request(path,body){
 const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Session':token},body:JSON.stringify(body)};
 const response=await fetch(path,{...options,cache:'no-store'});
 const value=await response.json();if(!response.ok)throw Error(value.error??`HTTP ${response.status}`);return value;
}
function sensorsFresh(){const ages=liveState?.sensor_age_ms??{};return ['yaw','pitch'].every(axis=>Number.isFinite(ages[axis])&&ages[axis]+Date.now()-stateAt<=500);}
function controlsReady(){return liveState&&sensorsFresh()&&Object.entries(liveState.command_paths??{}).some(([id,state])=>state==='CONNECTED'&&(!networkState||networkState.paths[id]?.up));}
function press(key){if(controlsReady()){drag=null;dirty=false;view.classList.remove('dragging');keys.add(key);velocityDirty=true;}}
function release(key){if(keys.delete(key))velocityDirty=true;}
function stopInput(){keys.clear();velocityDirty=true;drag=null;view.classList.remove('dragging');}
window.addEventListener('keydown',e=>{if(e.key in {ArrowUp:1,ArrowDown:1,ArrowLeft:1,ArrowRight:1}){e.preventDefault();press(e.key);}});
window.addEventListener('keyup',e=>release(e.key));window.addEventListener('blur',stopInput);
document.querySelectorAll('[data-direction]').forEach(button=>{
 const key=keyMap[button.dataset.direction];
 button.onpointerdown=e=>{e.preventDefault();button.setPointerCapture(e.pointerId);press(key);};
 button.onpointerup=button.onpointercancel=button.onlostpointercapture=()=>release(key);
});
image.draggable=false;image.ondragstart=e=>e.preventDefault();view.ondragstart=e=>e.preventDefault();
view.onpointerdown=e=>{if(e.button!==0||!controlsReady())return;e.preventDefault();keys.clear();dx=liveState.sensors.yaw.degrees;dy=liveState.sensors.pitch.degrees;drag=[e.clientX,e.clientY];dirty=true;view.setPointerCapture(e.pointerId);view.classList.add('dragging');};
view.onpointermove=e=>{if(!drag)return;dx=(dx+(drag[0]-e.clientX)*.15+540)%360-180;dy=Math.max(-75,Math.min(75,dy+(e.clientY-drag[1])*.15));dirty=true;drag=[e.clientX,e.clientY];};
view.onpointerup=view.onpointercancel=view.onlostpointercapture=()=>{drag=null;dirty=false;velocityDirty=true;view.classList.remove('dragging');};
byId('reset').onclick=()=>{if(!controlsReady())return;stopInput();dx=dy=0;dirty=false;centre=true;};
setInterval(async()=>{
 if(commandBusy)return;
 if(!controlsReady()){keys.clear();dx=dy=0;dirty=centre=velocityDirty=false;return;}
 let value;
 if(centre){value={mode:'absolute',yaw:0,pitch:0};centre=false;velocityDirty=false;}
 else if(drag){value={mode:'target',yaw:dx,pitch:dy};dirty=false;}
 else if(keys.size||velocityDirty){value={mode:'velocity',yaw_rate:(keys.has('ArrowRight')-keys.has('ArrowLeft'))*25,pitch_rate:(keys.has('ArrowUp')-keys.has('ArrowDown'))*25};velocityDirty=false;}
 else return;
 commandBusy=true;
 try{await request('/command',value);}catch(e){byId('last-command').textContent='Command failed';byId('command-detail').textContent=e.message;}finally{commandBusy=false;}
},50);
function renderNetwork(){
 if(!networkState){byId('link-message').textContent='Network controller unavailable';return;}
 const icons={a:'wifi',b:'mobile',c:'satellite'};
 const markup=Object.entries(networkState.paths).map(([id,p])=>{
  return `<div id="link-${id}" class="link ${p.up?'':'off'}"><div class="link-info"><div class="link-top"><span class="link-name">${ShenDemo.icon(icons[id])}${p.name}</span><span id="state-${id}" class="link-state">${!p.up?'CUT':'ENABLED'}</span></div><div class="link-metrics"><span>RTT profile <b>${p.rtt_ms} ms</b></span><span>Configured upload / download <b>${p.uplink_mbps} / ${p.downlink_mbps} Mbps</b></span></div></div><button data-path="${id}" ${networkBusy||networkState.busy||autoActive?'disabled':''}>${p.up?'Cut':'Restore'}</button></div>`;
 }).join('');
 if(markup!==linkMarkup){byId('links').innerHTML=markup;linkMarkup=markup;}
 for(const [id,p] of Object.entries(networkState.paths)){const current=p.up&&lastPath===id&&liveState&&!liveState.stale;byId('link-'+id).classList.toggle('primary',!!current);byId('state-'+id).textContent=!p.up?'CUT':current?'FRAME VIA':'ENABLED';}
 const available=Object.values(networkState.paths).filter(p=>p.up).length;
 byId('link-message').textContent=`${available} paths enabled · video path identifies the received frame, not a fixed primary. Simulated network profiles.`;
 byId('auto-status').textContent=networkError?`Network change failed: ${networkError}`:`${autoActive?'AUTO':'MANUAL'} · ${networkBusy||networkState.busy?'verifying change · ':''}${networkState.pulses??0} completed auto cuts · measured session ${(networkState.pulse_rate??0).toFixed(2)} /s${lastAutoPath?' · last cut '+(networkState.paths[lastAutoPath]?.name??lastAutoPath):''}`;
}
document.querySelectorAll('input[name="cut-mode"]').forEach(input=>{input.disabled=false;if(input.value==='auto'&&input.nextSibling)input.nextSibling.textContent=' Auto';input.onchange=()=>{autoActive=input.value==='auto'&&input.checked;autoNext=0;renderNetwork();};});
for(const [id,label,unit] of [['cut-rate','rate-value',' /s requested'],['cut-duration','duration-value',' ms hold']]){byId(id).disabled=false;byId(id).oninput=()=>{byId(label).textContent=byId(id).value+unit;};}
byId('cut-duration').max='5000';byId('cut-duration').step='50';
setInterval(async()=>{
 if(!autoActive||networkBusy||networkState?.busy||Date.now()<autoNext)return;
 if(!liveState||liveState.stale||Date.now()-stateAt>1000||!networkState||!networkState.paths[lastPath]?.up||Object.values(networkState.paths).filter(p=>p.up).length<2)return;
 const start=Date.now(),path=lastPath;
 networkBusy=true;networkError='';renderNetwork();
 try{networkState=await request('/network',{path,duration_ms:Number(byId('cut-duration').value)});lastAutoPath=networkState.cut_frame_path??path;}
 catch(error){autoActive=false;networkError=error.message;document.querySelectorAll('input[name="cut-mode"]').forEach(input=>input.checked=input.value==='manual');}
 finally{networkBusy=false;autoNext=Math.max(Date.now()+100,start+1000/Number(byId('cut-rate').value));renderNetwork();}
},100);
document.addEventListener('visibilitychange',()=>{if(document.hidden){autoActive=false;document.querySelectorAll('input[name="cut-mode"]').forEach(input=>input.checked=input.value==='manual');renderNetwork();}});
byId('links').addEventListener('click',async e=>{
 const path=e.target.closest('[data-path]')?.dataset.path;if(!path||autoActive||networkBusy||!networkState||networkState.busy)return;
 networkBusy=true;networkError='';renderNetwork();
 try{networkState=await request('/network',{path,up:!networkState.paths[path].up});}
 catch(error){networkError=error.message;}
 finally{networkBusy=false;renderNetwork();}
});
async function pollState(){
 try{
  liveState=await request('/state');stateAt=Date.now();lastPath=liveState.last_frame_path;
  const yaw=liveState.sensors.yaw?.degrees,pitch=liveState.sensors.pitch?.degrees;
  const sensorStale=!sensorsFresh();
  byId('heading').textContent=`${sensorStale?'STALE SENSOR DATA · ':''}HEADING ${signed(yaw)} · PITCH ${signed(pitch)}`;
  byId('heading-value').textContent=signed(yaw);byId('pitch-value').textContent=signed(pitch);
  byId('overlay-path').textContent=liveState.stale?'—':networkState?.paths[lastPath]?.name??lastPath??'—';
  view.classList.toggle('no-link',liveState.stale);
  view.dataset.videoStatus=networkState&&Object.values(networkState.paths).every(p=>!p.up)?'ALL PATHS INTERRUPTED — WAITING FOR CONNECTIVITY':'VIDEO DELAYED — PATH MAY STILL BE ENABLED';
  document.querySelector('.preview-chip').textContent=liveState.stale?'STATION · NO FRESH VIDEO':'STATION · RECEIVING ROBOT';
  byId('last-command').textContent=`Command ${liveState.sent} · robot echo ${liveState.sensors.yaw?.command_id??'—'}`;
  byId('command-detail').textContent=`Control: ${controlsReady()?'ready':'waiting for fresh sensors / command path'} · Receipt: ${liveState.receipts?.[String(liveState.sent)]??'—'} · video marker ${liveState.witness??'—'}`;
  byId('live-status').textContent=liveState.stale?'NO FRESH VIDEO':'RECEIVING ROBOT VIDEO';
  byId('stat-fps').textContent=liveState.stale?'—':Number(liveState.sdk_receive_fps??0).toFixed(1)+' FPS';
  byId('stat-resolution').textContent=liveState.resolution?.join('×')??'—';
  byId('stat-age').textContent=Number.isFinite(liveState.video_age_ms)?Math.round(liveState.video_age_ms)+' ms':'—';
  byId('stat-gap').textContent=Number.isFinite(liveState.max_video_gap_ms)?Math.round(liveState.max_video_gap_ms)+' ms':'—';
  byId('stat-bitrate').textContent=liveState.video_bitrate_max_kbps?liveState.video_bitrate_max_kbps+' kbps':'—';
  byId('stat-sensors').textContent=sensorStale?'Stale':'Yaw / pitch';
  byId('stat-frames').textContent=String(liveState.frames);
  byId('stat-error').textContent=[...liveState.errors,liveState.preview_error].filter(Boolean).join('; ');
  byId('stat-error').hidden=!byId('stat-error').textContent;
  if(!liveState.stale&&!image.dataset.streaming){image.src='/stream';image.dataset.streaming='1';}
  renderNetwork();
 }catch(error){liveState=null;stopInput();view.classList.add('no-link');view.dataset.videoStatus='STATION SERVER UNREACHABLE';document.querySelector('.preview-chip').textContent='STATION · DISCONNECTED';byId('overlay-path').textContent='—';byId('live-status').textContent='STATION DISCONNECTED';for(const id of ['stat-fps','stat-resolution','stat-age','stat-gap','stat-bitrate','stat-sensors','stat-frames'])byId(id).textContent='—';byId('stat-error').hidden=false;byId('stat-error').textContent=error.message;}
 setTimeout(pollState,300);
}
async function pollNetwork(){
 try{networkState=await request('/network');renderNetwork();}
 catch(error){networkState=null;linkMarkup='';byId('links').innerHTML='';byId('link-message').textContent='Network controller unavailable: '+error.message;}
 setTimeout(pollNetwork,500);
}
image.onerror=()=>{delete image.dataset.streaming;};
pollState();pollNetwork();

// A compact readout for existing manual / auto controls. No additional cut workflow.
let watchedCut=null,previousPaths=null;
function observeTransition(){
 const output=byId('transition-result');if(!output)return;
 if(!networkState||!liveState||Date.now()-stateAt>1000){output.textContent='Waiting for current network and video status…';previousPaths=null;watchedCut=null;return;}
 const paths=networkState.paths;
 if(previousPaths){
  const newCuts=Object.keys(paths).filter(id=>previousPaths[id]&&!paths[id].up);
  if(newCuts.length)watchedCut={path:newCuts.at(-1),frame:liveState.last_frame_id,session:liveState.session_id,observed:false};
 }
 previousPaths=Object.fromEntries(Object.entries(paths).map(([id,p])=>[id,p.up]));
 if(Object.values(paths).every(p=>!p.up)){output.textContent='All paths interrupted — waiting for connectivity.';watchedCut=null;return;}
 if(!watchedCut)return;
 const name=paths[watchedCut.path]?.name??watchedCut.path;
 if(!paths[watchedCut.path]?.up&&!liveState.stale&&paths[liveState.last_frame_path]?.up&&liveState.last_frame_path!==watchedCut.path&&(liveState.last_frame_id!==watchedCut.frame||liveState.session_id!==watchedCut.session)){
  watchedCut.observed=true;
  output.textContent=name+' interrupted → new video received via '+paths[liveState.last_frame_path].name+'.';
 }else if(!watchedCut.observed){
  output.textContent=paths[watchedCut.path]?.up?name+' restored · alternate video not verified by this page.':name+' interrupted · waiting for new video on another path…';
 }
}
setInterval(observeTransition,200);
