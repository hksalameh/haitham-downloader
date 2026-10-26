"use strict";
// Intercepts only deliberate clicks on ordinary direct-file links, before
// Chrome opens its native Save As prompt. Other downloads remain with Chrome.
let enabled=false;
chrome.storage.local.get({autoCapture:true},v=>{enabled=v.autoCapture===true;});
chrome.storage.onChanged.addListener((changes,area)=>{
  if(area==="local" && changes.autoCapture) enabled=changes.autoCapture.newValue===true;
});
const filePattern=/\.(?:zip|7z|rar|tar|gz|bz2|xz|iso|exe|msi|msix|apk|deb|rpm|dmg|pkg|jar|pdf|docx?|xlsx?|pptx?|csv|mp3|m4a|wav|flac|mp4|mkv|avi|mov|webm|txt)(?:$|[?#])/i;
document.addEventListener("click",event=>{
  if(!enabled || !event.isTrusted || event.defaultPrevented || event.button!==0 ||
     event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link=event.target instanceof Element ? event.target.closest("a[href]") : null;
  if(!link) return;
  const url=link.href;
  if(!/^https?:\/\//i.test(url) || (!link.hasAttribute("download") && !filePattern.test(url))) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  let filename=(link.getAttribute("download")||"").trim();
  if(!filename) {
    try { filename=decodeURIComponent(new URL(url).pathname.split("/").pop()||""); }
    catch { filename=""; }
  }
  chrome.runtime.sendMessage({action:"smartLink",url,filename},reply=>{
    if(chrome.runtime.lastError || (!reply?.ok && !reply?.fallback)) {
      // Extension error: don't silently lose the requested download.
      window.location.assign(url);
    }
  });
},true);
