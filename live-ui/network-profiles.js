'use strict';
// Presentation names assigned to the existing video benchmark profiles.
// These are scenario labels, not identification of physical radio interfaces.
window.ShenDemo=Object.freeze({
  source:Object.freeze({width:1920,height:1080,fps:25,codec:'H264'}),
  paths:Object.freeze({
    A:Object.freeze({name:'Wi-Fi',rttMs:10,uplinkMbps:10,icon:'wifi'}),
    B:Object.freeze({name:'Mobile',rttMs:20,uplinkMbps:6,icon:'mobile'}),
    C:Object.freeze({name:'Satellite',rttMs:60,uplinkMbps:4,icon:'satellite'})
  }),
  icon(kind){
    const drawings={
      wifi:'<path d="M3 8a14 14 0 0 1 18 0M6 12a9 9 0 0 1 12 0M9 16a4 4 0 0 1 6 0"/><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/>',
      mobile:'<path d="M8 21l4-15 4 15M6 21h12M9 16h6M10 12h4M6 4a8 8 0 0 0 0 10M18 4a8 8 0 0 1 0 10M9 6a4 4 0 0 0 0 5M15 6a4 4 0 0 1 0 5"/>',
      satellite:'<path d="M3 9a12 12 0 0 0 12 12L3 9ZM9 15l6-6M14 4a6 6 0 0 1 6 6M14 1a9 9 0 0 1 9 9M7 18l-2 4h11l-2-3"/><circle cx="16" cy="8" r="1"/>'
    };
    return `<svg class="network-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${drawings[kind]||drawings.wifi}</svg>`;
  }
});
