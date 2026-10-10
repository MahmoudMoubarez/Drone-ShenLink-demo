// Executes the actual generated viewer script against a small browser stub.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const script=fs.readFileSync(0,'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function test(stale=false,broken=false){
  const elements={image:{style:{},dataset:{},classList:{add(){},remove(){}},setPointerCapture(){}},state:{},reset:{}};
  const listeners={},calls=[],timers=[];let interval;
  const sandbox={document:{getElementById:id=>elements[id],querySelectorAll:()=>[]},
    window:{addEventListener:(name,fn)=>listeners[name]=fn},
    URL:{createObjectURL:()=> 'blob:received-frame',revokeObjectURL(){}},
    setInterval:fn=>{interval=fn;},setTimeout:fn=>timers.push(fn),
    fetch:async(path,options)=>{
      calls.push({path,options});
      if(broken)throw Error('connection lost');
      return {ok:true,json:async()=>({stale,frames:9,sensors:{yaw:{degrees:10,command_id:1},pitch:{degrees:0}},sent:1,witness:1,errors:[]}),blob:async()=>({})};
    }};
  vm.runInNewContext(script,sandbox);
  await settle();await settle();
  if(broken){assert.match(elements.state.textContent,/Disconnected/);return;}
  if(stale){assert.match(elements.state.textContent,/STALE/);assert(!calls.some(c=>c.path==='/frame'));return;}
  assert.equal(elements.image.src,'/stream');
  assert.equal(elements.image.draggable,false);
  let prevented=false;elements.image.ondragstart({preventDefault(){prevented=true;}});assert(prevented);
  assert.match(elements.state.textContent,/RECEIVING VIDEO/);
  listeners.keydown({key:'ArrowRight',preventDefault(){}});
  await interval();
  const command=calls.find(c=>c.path==='/command');
  assert.deepEqual(JSON.parse(command.options.body),{mode:'velocity',yaw_rate:25,pitch_rate:0});
  await interval();assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,25);
  listeners.keyup({key:'ArrowRight'});
  await interval();assert.equal(JSON.parse(calls.at(-1).options.body).yaw_rate,0);
  const before=calls.length;await interval();assert.equal(calls.length,before);
  elements.reset.onclick();await interval();
  assert.equal(JSON.parse(calls.at(-1).options.body).yaw,0);
  prevented=false;
  elements.image.onpointerdown({button:0,clientX:100,clientY:100,pointerId:1,preventDefault(){prevented=true;}});
  assert(prevented);
  elements.image.onpointermove({clientX:80,clientY:110});await interval();
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),{mode:'delta',yaw_delta:3,pitch_delta:1.5});
  elements.image.onpointerup();
  const ended=calls.length;elements.image.onpointermove({clientX:30,clientY:40});await interval();assert.equal(calls.length,ended);
  listeners.keydown({key:'ArrowUp',preventDefault(){}});await interval();
  listeners.blur();await interval();assert.equal(JSON.parse(calls.at(-1).options.body).pitch_rate,0);
}
(async()=>{await test();await test(true);await test(false,true);
  console.log('PASS: generated JS, frame display, arrow send/release, centre, stale and disconnection states.');
})().catch(error=>{console.error(error);process.exitCode=1;});
