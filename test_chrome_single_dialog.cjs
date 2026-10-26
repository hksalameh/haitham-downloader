"use strict";
const assert=require("node:assert/strict");
const vm=require("node:vm");
const fs=require("node:fs");
const path=require("node:path");
const background=fs.readFileSync(path.join(__dirname,"browser-extension","background.js"),"utf8");
const content=fs.readFileSync(path.join(__dirname,"browser-extension","content.js"),"utf8");
let createdListeners=0, sent=[], fallback=0;
const callbacks={};
const chrome={
 runtime:{
  onInstalled:{addListener:fn=>callbacks.installed=fn},
  onMessage:{addListener:fn=>callbacks.onMessage=fn},
  sendMessage:(data,done)=>{sent.push(data);done({ok:true});},
  lastError:null,
 },
 contextMenus:{removeAll:()=>Promise.resolve(),create:()=>{},onClicked:{addListener:()=>{}}},
 notifications:{create:()=>Promise.resolve()},
 storage:{local:{
  get:async()=>({autoCapture:true}),
  set:()=>Promise.resolve(),
 },onChanged:{addListener:()=>{}}},
 downloads:{
  onCreated:{addListener:()=>{createdListeners++}},
  download:async()=>{fallback++;return 42;},
 }
};
const sandbox={chrome,fetch:async(url,opts)=>{
 if(url.endsWith("/browser/add"))return {ok:true,json:async()=>({ok:true})};
 return {ok:true,json:async()=>({ok:true,version:"4.1-classic-preview"})};
},console,AbortController,setTimeout,clearTimeout};
vm.runInNewContext(background,sandbox);
assert.equal(createdListeners,0,"Late interception would trigger Chrome's Save dialog too");
assert.equal(typeof callbacks.onMessage,"function");
(async()=>{
 let result;
 await new Promise(resolve=>{
  callbacks.onMessage({action:"smartLink",url:"https://example.org/file.zip",filename:"file.zip"},null,value=>{result=value;resolve()});
 });
 assert.equal(result.ok,true);
 assert.equal(fallback,0);
 // content script must prevent native Chrome download before program dialog
 const clicks=[],listeners={};
 const link={
  href:"https://example.org/file.zip",
  hasAttribute:()=>false,
  getAttribute:()=>null,
 };
 const fakeElement=function(){};
 fakeElement.prototype.closest=function(){return link;};
 const contentChrome={
  storage:{local:{get:(_defaults,callback)=>callback({autoCapture:true})},onChanged:{addListener:()=>{}}},
  runtime:{sendMessage:(_obj,callback)=>callback({ok:true}),lastError:null}
 };
 const ctx={
  chrome:contentChrome,console,Element:fakeElement,
  document:{addEventListener:(name,cb)=>listeners[name]=cb},
  window:{location:{assign:()=>{throw Error("Unexpected browser navigation")}}},
  URL,
 };
 vm.runInNewContext(content,ctx);
 assert.equal(typeof listeners.click,"function");
 const event={isTrusted:true,defaultPrevented:false,button:0,ctrlKey:false,metaKey:false,shiftKey:false,altKey:false,
  target:new fakeElement(),preventDefault:()=>clicks.push("prevent"),stopImmediatePropagation:()=>clicks.push("stop")};
 listeners.click(event);
 assert.deepEqual(clicks,["prevent","stop"]);
 console.log("PASS no late downloads.onCreated; direct links intercepted before Chrome Save As");
})().catch(e=>{console.error(e);process.exit(1)});
