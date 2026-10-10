const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const script=fs.readFileSync(__dirname+'/live-ui/live.js','utf8');
const html=fs.readFileSync(__dirname+'/live-ui/index.html','utf8');
assert(!html.includes('test-active'));assert(!html.includes('judge-test'));assert(html.includes('transition-result'));
const output={textContent:'Manual / Auto'},intervals=[];
const sandbox={Date,byId:()=>output,setInterval:fn=>intervals.push(fn),stateAt:Date.now(),
 liveState:{stale:false,last_frame_id:10,session_id:'s',last_frame_path:'a'},
 networkState:{paths:{a:{up:true,name:'Wi-Fi'},b:{up:true,name:'Mobile'},c:{up:true,name:'Satellite'}}}};
vm.runInNewContext(script.slice(script.indexOf('// A compact readout')),sandbox);
const poll=intervals[0];poll();
sandbox.networkState.paths.a.up=false;poll();
assert(output.textContent.includes('waiting for new video'));
sandbox.liveState.last_frame_path='b';poll();
assert(!output.textContent.includes('new video received'));
sandbox.liveState.last_frame_id=11;poll();
assert.equal(output.textContent,'Wi-Fi interrupted → new video received via Mobile.');
sandbox.networkState.paths.b.up=false;sandbox.networkState.paths.c.up=false;poll();
assert(output.textContent.includes('All paths interrupted'));
sandbox.networkState=null;poll();assert(output.textContent.includes('Waiting for current'));
console.log('PASS: redundant controls removed; manual/auto readout requires a verified cut and new alternate frame; all-cut/disconnected states');
