'use strict';
const element=id=>document.getElementById(id);
async function refreshLibrary(){
 try{
  const responses=await Promise.all([fetch('/state',{cache:'no-store'}),fetch('/network',{cache:'no-store'})]);
  if(responses.some(r=>!r.ok))throw Error('Backend unavailable');
  const [state,network]=await Promise.all(responses.map(r=>r.json()));
  element('library-paths').replaceChildren();
  for(const path of Object.values(network.paths)){
   const row=document.createElement('p');row.className='endpoint-note';
   row.textContent=path.name+' · '+(path.up?'Enabled':'Cut')+' · configured upload / download '+path.uplink_mbps+' / '+path.downlink_mbps+' Mbps · RTT '+path.rtt_ms+' ms';
   element('library-paths').appendChild(row);
  }
  element('library-resolution').textContent=state.resolution?.join(' × ')??'No received frame';
  element('library-video').textContent=(state.stale?'No fresh video':'Receiving')+' · '+Number(state.sdk_receive_fps??0).toFixed(1)+' FPS · bitrate ceiling '+state.video_bitrate_max_kbps+' kbps';
  element('library-feedback').textContent='Commands sent '+state.sent+' · latest receipt '+(state.receipts?.[String(state.sent)]??'—')+' · robot yaw echo '+(state.sensors.yaw?.command_id??'—');
  element('config-status').textContent='Live session · '+state.frames+' received frames. Change paths on Robot or Station.';
 }catch(error){element('config-status').textContent=error.message;element('library-video').textContent='Backend unavailable';element('library-feedback').textContent='No current feedback';}
 setTimeout(refreshLibrary,750);
}
refreshLibrary();
