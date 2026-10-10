const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const root=__dirname+'/live-ui/',html=fs.readFileSync(root+'robot.html','utf8');
assert(!html.includes('data-component="camera"'));
assert(!html.includes('data-component="sensor"'));
assert.equal((html.match(/TEST SOURCE/g)||[]).length,2);
assert(html.includes('id="source-image"'));
const elements={},timers=[];
const element=id=>elements[id]??={hidden:false,classList:{toggle(){}},textContent:''};
let stale=false,failed=false;
const sandbox={document:{getElementById:element,querySelectorAll:()=>[],querySelector:q=>q.startsWith('meta')?{content:'test'}:element(q)},setTimeout:fn=>timers.push(fn),Date,
 fetch:async url=>({ok:!failed,json:async()=>url==='/network'?{paths:{}}:{stale,sensors:{yaw:{command_id:1}},sensor_age_ms:{yaw:20,pitch:21}}})};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 vm.runInNewContext(fs.readFileSync(root+'robot-live.js','utf8'),sandbox);await settle();
 const image=element('source-image'),placeholder=element('source-placeholder');
 assert(image.src.startsWith('/frame?t='));image.onload();assert.equal(image.hidden,false);assert.equal(placeholder.hidden,true);
 stale=true;await timers.shift()();assert.equal(image.hidden,true);assert.equal(placeholder.textContent,'No fresh video');
 stale=false;await timers.shift()();image.onerror();assert.equal(placeholder.textContent,'Preview unavailable');
 failed=true;await timers.shift()();assert.equal(image.hidden,true);assert.equal(placeholder.textContent,'Disconnected');
 console.log('PASS: read-only sources, received thumbnail, stale/error/disconnected states');
})().catch(e=>{console.error(e);process.exitCode=1;});
