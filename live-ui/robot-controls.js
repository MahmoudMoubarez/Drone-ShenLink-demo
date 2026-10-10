'use strict';

// Anchor the callouts to image coordinates so they stay attached across screen sizes.
const map=document.querySelector('.device-map');
const drone=document.querySelector('.drone-image');
function positionConnections(){
  const bounds=map.getBoundingClientRect(),image=drone.getBoundingClientRect();
  if(!bounds.width||!bounds.height)return;
  document.querySelector('.component-lines').setAttribute('viewBox',`0 0 ${bounds.width} ${bounds.height}`);
  const point=(x,y)=>({x:image.left-bounds.left+x*image.width,y:image.top-bounds.top+y*image.height});
  const anchors={camera:point(.63,.35),sensor:point(.50,.55),radio:point(.55,.16)};
  const connect=(component,anchor,right)=>{
    const card=document.getElementById(`${component}-card`).getBoundingClientRect();
    const end={x:(right?card.left:card.right)-bounds.left,y:card.top-bounds.top+card.height/2};
    // Separate curved leaders instead of overlapping vertical trunks.
    const reach=Math.max(35,Math.abs(anchor.x-end.x)*.42);
    const direction=right?-1:1;
    document.querySelector(`#${component}-line path`).setAttribute('d',`M ${end.x},${end.y} C ${end.x+direction*reach},${end.y} ${anchor.x-direction*reach},${anchor.y} ${anchor.x},${anchor.y}`);
    const dot=document.querySelector(`#${component}-line circle`);
    if(dot){dot.setAttribute('cx',String(anchor.x));dot.setAttribute('cy',String(anchor.y));}
  };
  connect('camera',anchors.camera,false);
  connect('sensor',anchors.sensor,false);
  ['A','B','C'].forEach(path=>connect(`link-${path}`,anchors.radio,true));
  const radio=document.querySelector('#radio-node circle');
  radio.setAttribute('cx',String(anchors.radio.x));radio.setAttribute('cy',String(anchors.radio.y));
}
drone.addEventListener('load',positionConnections);
window.addEventListener('resize',positionConnections);
new ResizeObserver(positionConnections).observe(map);
positionConnections();
