const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const root=__dirname+'/live-ui/';
const html=fs.readFileSync(root+'index.html','utf8'),script=fs.readFileSync(root+'live.js','utf8');
assert(!html.includes('const state={links:'));
assert(html.includes('id="image"')&&html.includes('DEMO REPLAY'));
assert(html.includes('value="auto" disabled'));
assert(!script.includes('requestAnimationFrame')&&!script.includes('backgroundPosition'));
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const elements={},events={},intervals=[],timers=[],calls=[];
 function element(id){return elements[id]??= {style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},addEventListener:(name,fn)=>events[id+':'+name]=fn,setPointerCapture(){}};}
 let state={stale:false,frames:25,sensors:{yaw:{degrees:12,command_id:2},pitch:{degrees:-3}},
  sensor_age_ms:{yaw:20,pitch:20},receipts:{'2':'RECEIVED'},sent:2,witness:2,errors:[],
  sdk_receive_fps:24,resolution:[640,360],last_frame_path:'b',command_paths:{a:'CONNECTED',b:'CONNECTED',c:'CONNECTED'}};
 const network={paths:{a:{name:'Wi-Fi',up:true,rtt_ms:10,uplink_mbps:6,downlink_mbps:30},
  b:{name:'Mobile',up:true,rtt_ms:20,uplink_mbps:4,downlink_mbps:50},c:{name:'Satellite',up:true,rtt_ms:60,uplink_mbps:2.5,downlink_mbps:25}},changes:0,busy:false};
 let cutResolve;
 element('cut-rate').value='1';element('cut-duration').value='100';
 const radios=[{value:'manual',checked:true},{value:'auto',checked:false}];
 const sandbox={document:{addEventListener:(name,fn)=>events['document:'+name]=fn,getElementById:element,querySelector:q=>q.startsWith('meta')?{content:'test-token'}:element('chip'),querySelectorAll:q=>q.includes('cut-mode')?radios:[]},
  window:{addEventListener:(name,fn)=>events[name]=fn},ShenDemo:{icon:()=>''},
  setInterval:fn=>intervals.push(fn),setTimeout:fn=>timers.push(fn),
  fetch:async(path,options)=>{
   calls.push({path,options});
   if(path==='/network'&&options.method==='POST'){
    const body=JSON.parse(options.body);
    if('up' in body)await new Promise(resolve=>cutResolve=()=>{network.paths[body.path].up=body.up;network.changes++;resolve();});
    else network.pulses=(network.pulses??0)+1;
   }
   const value=path==='/state'?state:path==='/network'?network:{id:3};
   return {ok:true,json:async()=>JSON.parse(JSON.stringify(value))};
  }};
 vm.runInNewContext(script,sandbox);await settle();await settle();
 assert.equal(element('image').src,'/stream');
 assert.equal(element('heading-value').textContent,'+12.0°');
 assert.equal(element('pitch-value').textContent,'-3.0°');
 assert.equal(element('stat-fps').textContent,'24.0 FPS');
 assert.equal(element('stat-resolution').textContent,'640×360');
 assert.equal(element('stat-sensors').textContent,'Yaw / pitch');
 assert(html.indexOf('class="video-stats"')<html.indexOf('<aside'));
 assert.equal((html.match(/id="live-status"/g)||[]).length,1);
 assert.equal(element('state-b').textContent,'FRAME VIA');
 assert.equal(radios[1].disabled,false);
 assert.equal(element('cut-duration').max,'5000');
 events.keydown({key:'ArrowRight',preventDefault(){}});await intervals[0]();
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{mode:'velocity',yaw_rate:25,pitch_rate:0});
 await intervals[0]();assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,25);
 events.keyup({key:'ArrowRight'});await intervals[0]();assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,0);
 const cut=events['links:click']({target:{closest:()=>({dataset:{path:'a'}})}});await settle();
 assert(element('links').innerHTML.includes('disabled'));
 assert(!element('links').innerHTML.includes('>CUT<'),'Must not invent cut state before server verification');
 cutResolve();await cut;assert(element('links').innerHTML.includes('>CUT<'));
 assert.equal(element('viewport').style.backgroundPosition,undefined);
 const view=element('viewport');view.onpointerdown({button:0,clientX:100,clientY:100,pointerId:1,preventDefault(){}});
 view.onpointermove({clientX:80,clientY:120});await intervals[0]();
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{mode:'target',yaw:15,pitch:0});
 await intervals[0]();assert.equal(JSON.parse(calls.at(-1).options.body).mode,'target','Held drag renews lease without new pointer events');
 view.onpointerup();await intervals[0]();assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,0);
 state={...state,stale:true};
 await timers[0]();
 events.keydown({key:'ArrowRight',preventDefault(){}});await intervals[0]();
 assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,25,'Video delay alone must not block healthy control');
 assert.equal(view.dataset.videoStatus,'VIDEO DELAYED — PATH MAY STILL BE ENABLED');
 state={...state,sensor_age_ms:{yaw:2000,pitch:2000}};
 await timers[0]();assert.match(element('live-status').textContent,/NO FRESH VIDEO/);
 assert.equal(element('stat-sensors').textContent,'Stale');
 const before=calls.filter(c=>c.path==='/command').length;
 events.keydown({key:'ArrowRight',preventDefault(){}});await intervals[0]();
 assert.equal(calls.filter(c=>c.path==='/command').length,before);
 state={...state,stale:false,sensor_age_ms:{yaw:20,pitch:20}};
 await timers[0]();
 radios[1].checked=true;radios[1].onchange();
 const autoTick=intervals[1]();await settle();
 assert.deepEqual(JSON.parse(calls.at(-1).options.body),{path:'b',duration_ms:100});
 await autoTick;
 radios[0].checked=true;radios[0].onchange();
 console.log('PASS: actual station JS, received angles, velocity/release/drag commands, no local rendering, verified-only cut state and stale handling.');
})().catch(e=>{console.error(e);process.exitCode=1;});
