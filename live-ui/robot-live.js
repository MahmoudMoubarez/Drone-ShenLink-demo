'use strict';
// Disable the preview handlers: no local state is evidence of a backend change.
const sessionToken=document.querySelector('meta[name="shen-session"]').content;
let actualNetwork=null,changing=false;
const sourceImage=document.getElementById('source-image'),sourcePlaceholder=document.getElementById('source-placeholder');
let thumbnailBusy=false,thumbnailFresh=false;
function hideThumbnail(message){sourceImage.hidden=true;sourcePlaceholder.hidden=false;sourcePlaceholder.textContent=message;}
sourceImage.onload=()=>{thumbnailBusy=false;if(thumbnailFresh){sourceImage.hidden=false;sourcePlaceholder.hidden=true;}};
sourceImage.onerror=()=>{thumbnailBusy=false;hideThumbnail('Preview unavailable');};
document.querySelectorAll('[data-component]').forEach(old=>{
 const button=old.cloneNode(true);old.replaceWith(button);button.disabled=true;
 if(!button.dataset.component.startsWith('link-'))button.querySelector('.switch-text').textContent='STATUS';
 else button.onclick=async()=>{
  if(changing||!actualNetwork)return;
  const path=button.dataset.component.slice(-1).toLowerCase();changing=true;
  try{const response=await fetch('/network',{method:'POST',headers:{'Content-Type':'application/json','X-Session':sessionToken},body:JSON.stringify({path,up:!actualNetwork.paths[path].up})});const data=await response.json();if(!response.ok)throw Error(data.error);}
  catch(error){document.getElementById('unit-summary').textContent=error.message;}
  finally{changing=false;}
 };
});
async function refreshActual(){
 try{
  const responses=await Promise.all([fetch('/state',{cache:'no-store'}),fetch('/network',{cache:'no-store'})]);
  if(responses.some(r=>!r.ok))throw Error('Backend unavailable');
  const [received,network]=await Promise.all(responses.map(r=>r.json()));actualNetwork=network;
  thumbnailFresh=!received.stale;
  if(!thumbnailFresh)hideThumbnail('No fresh video');
  else if(!thumbnailBusy){thumbnailBusy=true;sourceImage.src='/frame?t='+Date.now();}
  for(const [id,path] of Object.entries(network.paths)){
   const component='link-'+id.toUpperCase(),button=document.querySelector(`[data-component="${component}"]`);
   button.disabled=changing||network.busy;button.setAttribute('aria-checked',String(path.up));button.querySelector('.switch-text').textContent=path.up?'ON':'OFF';
   document.getElementById(component+'-card').classList.toggle('off',!path.up);document.getElementById(component+'-line').classList.toggle('off',!path.up);
   document.querySelector(`[data-latency="${id.toUpperCase()}"]`).textContent=path.rtt_ms+' ms';document.querySelector(`[data-bandwidth="${id.toUpperCase()}"]`).textContent=path.uplink_mbps+' Mbps';
  }
  document.getElementById('unit-summary').textContent=Object.values(network.paths).filter(p=>p.up).length+' / 3 paths enabled · verified kernel state';
  document.getElementById('camera-detail').textContent=received.stale?'No fresh video received (not proof camera is off)':'Video received at station';
  document.getElementById('sensor-detail').textContent='Test sensor stream · received freshness below';
  document.getElementById('source-status').textContent='Recorded 360° source · real SDK transport';
  document.getElementById('command-status').textContent='Robot echo: '+(received.sensors.yaw?.command_id??'—');
  document.getElementById('sensor-status').textContent='Yaw age '+Math.round(received.sensor_age_ms?.yaw??0)+' ms · pitch age '+Math.round(received.sensor_age_ms?.pitch??0)+' ms';
  document.querySelector('.preview-chip').textContent='ROBOT VIEW · LIVE TRANSPORT';
 }catch(error){actualNetwork=null;thumbnailFresh=false;hideThumbnail('Disconnected');document.querySelector('.preview-chip').textContent='BACKEND UNAVAILABLE';document.getElementById('unit-summary').textContent=error.message;document.querySelectorAll('[data-component]').forEach(b=>b.disabled=true);}
 setTimeout(refreshActual,500);
}
refreshActual();
