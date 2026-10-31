"use strict";
const $=id=>document.getElementById(id);
const UI={items:[],pending:[],selection:new Set(),filter:"all",query:"",options:{language:"ar",theme:"dark",download_dir:"",connections:8,concurrent:3},detailsId:null,detailsSnapshot:null,pendingId:null,busy:false, speed:0,menu:null,deleteIds:[]};
const NAV=[
 ["all","📁","كل التحميلات","All downloads",0],["archive","🗜","المضغوطة","Archives",1],
 ["document","▤","مستندات","Documents",1],["audio","♫","موسيقى","Music",1],
 ["software","▣","برامج","Programs",1],["video","▣","فيديو","Videos",1],
 ["other","📄","ملفات أخرى","Other",1],["completed","✓","المنتهية","Completed",0],
 ["active","⤓","قيد التحميل","Downloading",0],["paused","Ⅱ","المتوقفة","Paused",0],
 ["queued","⌛","الانتظار","Queued",0],["error","⚠","الأخطاء","Failed",0]
];
const T={
 "tasks":[["إضافة رابط جديد","add"],["تحميل فيديو","video"],["فتح مجلد التحميلات","folder"]],
 "file":[["فتح مجلد التحميلات","folder"],["حذف المحدد","delete"]],
 "download":[["استئناف","resume"],["إيقاف مؤقت","pause"],["إيقاف الكل","pause-all"],["حذف المكتمل","clear"]],
 "view":[["كل التحميلات","all"],["التحميلات النشطة","active"],["المكتملة","completed"],["الخيارات","settings"]],
 "help":[["عن مدير تحميل هيثم","about"]]
};
function en(){return UI.options.language==="en"}function tr(ar,eng){return en()?eng:ar}
const esc=t=>String(t??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
function size(n){let x=Number(n||0);let u=["B","KB","MB","GB","TB"];let i=0;while(x>=1024&&i<u.length-1){x/=1024;i++}return x.toFixed(i?1:0)+" "+u[i]}
function eta(item){let remain=Number(item.total||0)-Number(item.completed||0),sp=Number(item.speed||0);if(item.status==="complete")return "0s";if(sp<=0||remain<=0)return "—";let secs=Math.floor(remain/sp);return secs>3599?Math.floor(secs/3600)+"h "+Math.floor(secs%3600/60)+"m":Math.floor(secs/60)+"m "+(secs%60)+"s"}
function percent(d){return d.total>0?Math.max(0,Math.min(100,Math.round((d.completed||0)/d.total*100))):0}
function label(s){return ({active:tr("جارٍ التحميل","Downloading"),complete:tr("مكتمل","Complete"),paused:tr("متوقف مؤقتًا","Paused"),waiting:tr("بانتظار الدور","Queued"),error:tr("خطأ","Error"),missing:tr("الملف غير موجود","File missing")})[s]||s}
function alertToast(text,err=false){const el=$("toast");el.textContent=text;el.className="toast show"+(err?" error":"");clearTimeout(alertToast.timer);alertToast.timer=setTimeout(()=>el.className="toast",3500)}
async function api(path,data){
 const options={headers:{"X-Haitham-Token":window.HDM_TOKEN}};
 if(data!==undefined){options.method="POST";options.headers["Content-Type"]="application/json";options.body=JSON.stringify(data)}
 const r=await fetch(path,options);const body=await r.json();
 if(!r.ok)throw new Error(body.error||"HTTP "+r.status);
 return body.data===undefined?body:body.data
}
function filterItems(){
 return UI.items.filter(x=>{
 let st=x.status==="complete"?"completed":x.status==="waiting"?"queued":x.status;
 return (UI.filter==="all"||UI.filter===st||UI.filter===x.category)&&
 (!UI.query||(x.name+" "+(x.url||"")).toLowerCase().includes(UI.query.toLowerCase()))
 });
}
function buildNav(){
 const markup=NAV.map(([key,icon,ar,english,indent])=>{
 const count=UI.items.filter(d=>key==="all"||d.category===key||(key==="completed"&&d.status==="complete")||(key==="queued"&&d.status==="waiting")||d.status===key).length;
 return `<button data-nav="${key}" class="navitem ${indent?"child":""} ${UI.filter===key?"selected":""}"><span class="folder-icon">${icon}</span><b>${en()?english:ar}</b><small>${count}</small></button>`;
 }).join("");
 $("nav").innerHTML=markup;
}
function iconFor(kind){return ({archive:"▣",document:"▤",audio:"♫",video:"▶",software:"▦",other:"📄"})[kind]||"📄"}
function drawTable(){
 const rows=filterItems();
 const existing=new Set(UI.items.map(x=>x.gid));for(const id of UI.selection)if(!existing.has(id))UI.selection.delete(id);
 $("rows").innerHTML=rows.map(d=>{
   const pct=percent(d),id=esc(d.gid);
   const cls="state-"+(d.status||"waiting");
   const isSegmented=d.detail_type==="connections";
   const activeCount=Number(d.active_requests??0);
   const connection=isSegmented?(d.status==="active"?activeCount+"/"+d.connection_count:d.connection_count+tr(" جزء"," parts")):(d.connection_count||"—");
   const connectionHint=isSegmented?(d.status==="active"?tr("طلبات HTTP الجارية: ","Active HTTP requests: ")+activeCount+tr("، الحد الحالي: ","; current limit: ")+Number(d.connection_limit??0)+tr("، الأجزاء الكلية: ","; total segments: ")+d.connection_count:tr("عدد الأجزاء، وليس الاتصالات الجارية","Segment count, not active connections")):"";
   const state=d.status==="waiting"?"queued":d.status;
   const waitingForHost=d.status==="active"&&Number(d.retry_wait_seconds||0)>0;
   const displayState=waitingForHost?tr("انتظار الخادم ","Waiting for server ")+d.retry_wait_seconds+tr("ث","s"):label(state);
   return `<tr data-gid="${id}" class="${UI.selection.has(d.gid)?"selected":""}" title="${esc(d.url||d.filepath||"")}"><td><input type="checkbox" data-check="${id}" ${UI.selection.has(d.gid)?"checked":""}></td>
     <td><span class="filetitle"><span class="file-icon">${iconFor(d.category)}</span><span class="file-name">${esc(d.name)}</span></span></td>
     <td dir="ltr">${size(d.total||d.completed)}</td><td class="${cls}">${esc(displayState)}</td>
     <td><div class="percent-track"><div class="percent-fill" style="width:${pct}%"></div><span class="percent-txt">${pct}%</span></div></td>
     <td dir="ltr">${d.status==="active"?size(d.speed)+"/s":"—"}</td><td dir="ltr">${eta(d)}</td>
     <td dir="ltr" title="${esc(connectionHint)}">${esc(connection)}</td><td dir="ltr">${esc(d.date||"")}</td></tr>`;
 }).join("");
 $("empty").hidden=rows.length>0;
 $("counter").textContent=rows.length;
 $("selection-count").textContent=tr("محدد: "+UI.selection.size+" | ظاهر: "+rows.length,"Selected: "+UI.selection.size+" | Visible: "+rows.length);
 $("status-tally").textContent=tr("التحميلات: ","Downloads: ")+UI.items.length;
 $("overall-speed").textContent=tr("سرعة التحميل: ","Speed: ")+size(UI.speed)+"/s";
 const chosen=NAV.find(x=>x[0]===UI.filter);$("section-title").textContent=chosen?(en()?chosen[3]:chosen[2]):"";
 $("select-all").checked=rows.length>0&&rows.every(x=>UI.selection.has(x.gid));
}
async function browseFolder(inputId){
 const picker=window.pywebview?.api?.choose_folder;
 if(typeof picker!=="function"){
  alertToast(tr("اختيار المجلد متاح من نافذة البرنامج في Windows، وليس من Chrome.","Choose folders inside the Windows desktop app."),true);
  return;
 }
 const input=$(inputId);
 try{
  const chosen=await picker(input.value||UI.options.download_dir||"");
  if(typeof chosen==="string"&&chosen.length>0){input.value=chosen;input.dispatchEvent(new Event("input",{bubbles:true}));}
 }catch(e){alertToast("تعذر فتح استعراض المجلد: "+e.message,true)}
}
function optionSettings(){
 $("connections").value=UI.options.connections||8;
 $("concurrent").value=UI.options.concurrent||3;
 $("default-folder").value=UI.options.download_dir||"";
 $("temp-folder").value=UI.options.temp_dir||"";
 $("schedule-enabled").checked=!!UI.options.schedule_enabled;
 $("schedule-start").value=UI.options.schedule_start||"02:00";
 $("schedule-stop").value=UI.options.schedule_stop||"07:00";
 $("options-modal").hidden=false
}
function openAdd(params={}){
 UI.pendingId=params.pendingId||null;
 $("download-modal").hidden=false;
 $("url-input").value=params.url||"";
 $("name-input").value=params.filename||"";
 const last=UI.options.last_download_dir||"";
 $("folder-input").value=(UI.options.remember_last_folder&&last)?last:(UI.options.download_dir||"");
 $("remember-last-folder").checked=!!UI.options.remember_last_folder;
 $("kind").value=params.kind||"file";
 $("detailed-mode").checked=true;
 $("start-download").textContent=UI.pendingId?tr("استلام التحميل من Chrome","Receive Chrome Download"):tr("بدء التحميل","Start Download")
}
async function closeAdd(){
 const id=UI.pendingId;UI.pendingId=null;$("download-modal").hidden=true;
 if(id){try{await api("/api/resolve",{id,accept:false})}catch{}}
}
async function submitAdd(event){
 event.preventDefault();
 const link=$("url-input").value.trim(),name=$("name-input").value.trim(),directory=$("folder-input").value.trim(),kind=$("kind").value;
 const confirm=$("start-download");confirm.disabled=true;
 try {
    let response;
    if(UI.pendingId){
      response=await api("/api/resolve",{id:UI.pendingId,accept:true,filename:name,directory});
    }else {
      response=await api("/api/add",{url:link,filename:name,directory,kind,mode:$("detailed-mode").checked?"segmented":"aria2"})
    }
    try{
      const remember=$("remember-last-folder").checked;
      UI.options=await api("/api/settings",{remember_last_folder:remember,last_download_dir:remember?directory:""});
    }catch(prefErr){console.warn("Save folder preference failed",prefErr)}
    UI.pendingId=null;$("download-modal").hidden=true;
    alertToast(tr("بدأ التحميل في مدير هيثم","Download started"));
    await refresh();
    const gid=response.gid || UI.items.find(x=>x.url===link&&x.name===(name||x.name))?.gid;
    if(gid)openDetails(gid);
 }catch(err){alertToast(err.message,true)}
 finally{confirm.disabled=false}
}
function selectedIds(){return [...UI.selection]}
function openDelete(ids){if(!ids.length){alertToast("حدد ملفًا أو أكثر",true);return};UI.deleteIds=[...ids];$("delete-message").textContent="هل تريد إزالة "+ids.length+" تحميل؟";$("delete-from-disk").checked=false;$("delete-modal").hidden=false}
async function action(type,ids,more={}){
 if(!ids.length){alertToast("حدد تحميلًا أولاً",true);return}
 try {
  const result=await api("/api/action",{action:type,gids:ids,...more});
  if(type==="remove")ids.forEach(id=>UI.selection.delete(id));
  await refresh();
  alertToast("تم تنفيذ العملية على "+(result.affected||[]).length+" تحميل")
 }catch(err){alertToast(err.message,true)}
}
function closeDetail(){UI.detailsId=null;UI.detailsSnapshot=null;$("details-window").hidden=true}
function openDetails(gid){UI.detailsId=gid;$("details-window").hidden=false;refreshDetails()}
function fileMapConnections(segments){
 return [...segments].sort((a,b)=>a.start-b.start).map(s=>{const p=s.size?Math.min(100,Math.max(0,s.completed/s.size*100)):0;
 return `<div class="map-range ${p<100&&p>0?"is-working":""}" title="${esc(String(s.index))}: ${size(s.completed)} / ${size(s.size)}"><div class="done" style="width:${p}%"></div></div>`}).join("")
}
function rowsConnections(segments){
 return [...segments].sort((a,b)=>a.start-b.start).map(s=>{
 const p=s.size?Math.max(0,Math.min(100,s.completed/s.size*100)):0;
 return `<div class="conn-row"><span class="conn-range"><b>${s.index}</b><small>${size(s.start)} — ${size(s.end)} [HTTP Range]</small></span>
 <span class="conn-value">${size(s.completed)} / ${size(s.size)}</span><span class="conn-value">${size(s.speed)}/s</span>
 <span class="conn-progress ${p>=100?"complete":""}"><i style="width:${p}%"></i><em>${p.toFixed(1)}%</em></span></div>`
 }).join("")
}
function mapPieces(detail){
 const hex=String(detail.bitfield||"").toLowerCase();
 const pieces=Number(detail.numPieces)||0;
 if(!hex||!pieces)return {map:[],notes:"لا تتوفر خريطة الأجزاء لهذا التحميل."};
 const binary=hex.split("").map(char=>Number.parseInt(char,16).toString(2).padStart(4,"0")).join("").slice(0,pieces);
 const groups=Math.min(60,Math.max(1,Math.ceil(pieces/4))),ranges=[];
 for(let i=0;i<groups;i++){
   const a=Math.floor(i*pieces/groups),b=Math.floor((i+1)*pieces/groups);
   const slice=binary.slice(a,Math.max(b,a+1));
   let total=[...slice].filter(c=>c==="1").length;
   ranges.push({index:i+1,start:a*detail.pieceLength,end:(b*detail.pieceLength)-1,size:slice.length,completed:total,speed:0});
 }
 return {map:ranges,notes:"هذه خريطة أجزاء aria2 المكتملة، وليست إحصاءات بايتات كل اتصال. لإحصاءات الاتصال الفعلية استخدم وضع الاتصالات التفصيلي."};
}
async function refreshDetails(){
 const gid=UI.detailsId;if(!gid)return;
 try{
 const d=await api("/api/details?gid="+encodeURIComponent(gid));
 if(gid!==UI.detailsId)return;
 UI.detailsSnapshot=d;
 $("details-name").textContent=d.name||gid;$("details-title").textContent=(d.name||gid)+" — تقدم التحميل";
 $("details-status").textContent=d.status==="active"&&Number(d.retry_wait_seconds||0)>0?tr("انتظار الخادم ","Waiting for server ")+d.retry_wait_seconds+tr("ث","s"):label(d.status);
 $("details-size").textContent=size(d.total);
 $("details-downloaded").textContent=size(d.completed)+" / "+size(d.total);
 $("details-speed").textContent=size(d.speed)+"/s";$("details-eta").textContent=eta(d);
 $("details-percent").textContent=percent(d)+"%";
 $("details-progress-fill").style.width=percent(d)+"%";
 $("details-url").textContent=d.url||d.filepath||"";
 const connection=Array.isArray(d.ranges);
 $("details-connections-count").textContent=connection?(d.status==="active"?tr("طلبات HTTP الجارية: ","Active HTTP requests: ")+Number(d.active_requests??0)+tr("، الحد الحالي: ","; current limit: ")+Number(d.connection_limit??0)+tr("، الأجزاء: ","; total segments: ")+d.ranges.length:tr("أجزاء الملف: ","File segments: ")+d.ranges.length):tr("اتصالات aria2: ","aria2 connections: ")+(d.connections??"—");
 if(connection){
   $("segments-explanation").textContent=tr("كل سطر يمثل جزءًا من الملف، وليس اتصالًا نشطًا بالضرورة. الأرقام تعرض البايتات المستلمة لكل جزء.","Each row is a file segment, not necessarily a live connection. Byte counts are per segment.");
   $("file-map").innerHTML=fileMapConnections(d.ranges);
   $("connections-list").innerHTML=rowsConnections(d.ranges);
 }else {
   const parts=mapPieces(d);
   $("segments-explanation").textContent=parts.notes;
   $("file-map").innerHTML=fileMapConnections(parts.map.map(x=>({...x,size:x.size,completed:x.completed})));
   $("connections-list").innerHTML="<p class='hint'>"+esc(parts.notes)+"</p>";
 }
 $("details-footer").textContent=(d.status==="complete"?tr("اكتمل التحميل","Download complete"):d.status==="paused"?tr("متوقف مؤقتًا","Paused"):tr("التحميل جارٍ","Downloading"))+" • "+size(d.completed);
 $("details-pause").disabled=d.status!=="active"&&d.status!=="waiting";
 $("details-resume").disabled=d.status!=="paused";
 $("info-view").innerHTML="<div><b>الرابط:</b> "+esc(d.url||"")+"</div><div><b>الملف:</b> "+esc(d.filepath||"")+"</div><div><b>الحجم:</b> "+size(d.total)+"</div><div><b>الحالة:</b> "+label(d.status)+"</div><div><b>المحرك:</b> "+(connection?"HTTP Range connections":"aria2")+"</div>";
 }catch(e){$("details-footer").textContent=e.message}
}
async function refresh(){
 if(UI.busy)return;
 UI.busy=true;
 try{
 const data=await api("/api/state");
 UI.items=data.items||[];
 UI.pending=data.pending||[];
 UI.options=data.options||UI.options;
 UI.speed=data.speed||0;
 document.body.dataset.theme=UI.options.theme||"dark";
 $("connection-status").textContent=data.aria2_available===false
   ?tr("● التحميل المباشر جاهز","● Direct downloads ready")
   :(data.error?tr("● مشكلة المحرك","● Engine issue"):tr("● المحرك متصل","● Engine online"));
 $("connection-status").title=data.aria2_available===false
   ?tr("محرك HTTP Range يعمل؛ بعض الروابط تحتاج تثبيت aria2.","HTTP Range works; some links need aria2 installed."):(data.error||"");
 buildNav();drawTable();
 // Queue browser handoff when no local dialog is occupying the screen.
 if(UI.pendingId&&!UI.pending.some(p=>p.id===UI.pendingId)){UI.pendingId=null;$("download-modal").hidden=true}
 if(!UI.pendingId&&$("download-modal").hidden&&UI.pending.length){
   const incoming=UI.pending[0];openAdd({pendingId:incoming.id,url:incoming.url,filename:incoming.filename});
 }
 }catch(e){$("connection-status").textContent="● المحرك غير متصل";}
 finally{UI.busy=false}
}
function selectAll(){const ids=filterItems().map(d=>d.gid);const checked=$("select-all").checked;ids.forEach(id=>checked?UI.selection.add(id):UI.selection.delete(id));drawTable()}
async function menuAction(key){
 if(key==="add")openAdd();
 else if(key==="video")openAdd({kind:"video"});
 else if(key==="settings")optionSettings();
 else if(key==="folder")action("open_folder",UI.selection.size?selectedIds():UI.items.slice(0,1).map(i=>i.gid));
 else if(key==="delete")openDelete(selectedIds());
 else if(key==="clear")openDelete(UI.items.filter(x=>x.status==="complete").map(x=>x.gid));
 else if(key==="pause")action("pause",selectedIds());
 else if(key==="resume")action("resume",selectedIds());
 else if(key==="pause-all")action("pause",UI.items.filter(x=>x.status==="active").map(x=>x.gid));
 else if(key==="active"||key==="all"||key==="completed"){UI.filter=key;buildNav();drawTable()}
 else if(key==="about")alertToast("Haitham Downloader 4.1 Classic — مستقل ومجاني");
}
function showMenu(btn){
 document.querySelectorAll(".custom-menu").forEach(el=>el.remove());
 const entries=T[btn.dataset.menu]||[];
 const menu=document.createElement("div");menu.className="custom-menu";
 menu.style.cssText="position:fixed;z-index:30;background:var(--panel2);color:var(--text);border:1px solid var(--border);box-shadow:0 5px 16px #0007;padding:5px;border-radius:3px;min-width:170px";
 menu.innerHTML=entries.map(([label,act])=>`<button data-command="${act}" style="display:block;width:100%;text-align:right;color:inherit;background:transparent;border:0;padding:7px 9px">${esc(label)}</button>`).join("");
 const r=btn.getBoundingClientRect();menu.style.top=r.bottom+"px";menu.style.right=Math.max(5,document.documentElement.clientWidth-r.right)+"px";document.body.append(menu);
 menu.addEventListener("click",e=>{const key=e.target.closest("[data-command]")?.dataset.command;menu.remove();if(key)menuAction(key)});
}
function bind(){
 $("nav").addEventListener("click",e=>{const btn=e.target.closest("[data-nav]");if(!btn)return;UI.filter=btn.dataset.nav;UI.selection.clear();buildNav();drawTable()});
 $("rows").addEventListener("change",e=>{const box=e.target.closest("[data-check]");if(!box)return;box.checked?UI.selection.add(box.dataset.check):UI.selection.delete(box.dataset.check);drawTable()});
 $("rows").addEventListener("dblclick",e=>{const row=e.target.closest("[data-gid]");if(row)openDetails(row.dataset.gid)});
 $("rows").addEventListener("click",e=>{const row=e.target.closest("[data-gid]");if(row&&!e.target.closest("input")){UI.selection.clear();UI.selection.add(row.dataset.gid);drawTable()}});
 $("search").addEventListener("input",e=>{UI.query=e.target.value;drawTable()});
 $("select-all").addEventListener("change",selectAll);
 $("add-btn").addEventListener("click",()=>openAdd());
 $("video-btn").addEventListener("click",()=>openAdd({kind:"video"}));
 $("resume-btn").addEventListener("click",()=>action("resume",selectedIds()));
 $("pause-btn").addEventListener("click",()=>action("pause",selectedIds()));
 $("pause-all").addEventListener("click",()=>action("pause",UI.items.filter(x=>x.status==="active").map(x=>x.gid)));
 $("delete-selected").addEventListener("click",()=>openDelete(selectedIds()));
 $("clear-complete").addEventListener("click",()=>openDelete(UI.items.filter(x=>x.status==="complete").map(x=>x.gid)));
 $("settings").addEventListener("click",optionSettings);$("schedule").addEventListener("click",optionSettings);
 $("startqueue").addEventListener("click",()=>action("resume",UI.items.filter(x=>x.status==="paused").map(x=>x.gid)));
 $("stopqueue").addEventListener("click",()=>action("pause",UI.items.filter(x=>x.status==="active").map(x=>x.gid)));
 $("open-folder").addEventListener("click",()=>menuAction("folder"));
 $("theme").addEventListener("click",async()=>{try{UI.options=await api("/api/settings",{theme:UI.options.theme==="dark"?"light":"dark"});document.body.dataset.theme=UI.options.theme}catch(e){alertToast(e.message,true)}});
 $("language").addEventListener("click",async()=>{try{UI.options=await api("/api/settings",{language:en()?"ar":"en"});buildNav();drawTable()}catch(e){alertToast(e.message,true)}});
 document.querySelectorAll("[data-menu]").forEach(x=>x.addEventListener("click",()=>showMenu(x)));
 document.addEventListener("click",e=>{if(!e.target.closest(".custom-menu")&&!e.target.closest("[data-menu]"))document.querySelectorAll(".custom-menu").forEach(x=>x.remove())});
 document.querySelectorAll("[data-close]").forEach(x=>x.addEventListener("click",e=>{if(e.currentTarget.dataset.close==="download-modal")closeAdd();else $(e.currentTarget.dataset.close).hidden=true}));
 $("browse-folder").addEventListener("click",()=>browseFolder("folder-input"));
 $("reset-save-folder").addEventListener("click",()=>{
   $("folder-input").value=UI.options.download_dir||"";
   $("remember-last-folder").checked=false;
 });
 $("browse-default-folder").addEventListener("click",()=>browseFolder("default-folder"));
 $("browse-temp-folder").addEventListener("click",()=>browseFolder("temp-folder"));
 $("download-form").addEventListener("submit",submitAdd);
 $("settings-form").addEventListener("submit",async e=>{
  e.preventDefault();
  try{
   UI.options=await api("/api/settings",{connections:Number($("connections").value),concurrent:Number($("concurrent").value),download_dir:$("default-folder").value,temp_dir:$("temp-folder").value,
   schedule_enabled:$("schedule-enabled").checked,schedule_start:$("schedule-start").value,schedule_stop:$("schedule-stop").value});
   $("options-modal").hidden=true;alertToast("تم حفظ الخيارات");
  }catch(err){alertToast(err.message,true)}
 });
 $("confirm-delete").addEventListener("click",async()=>{$("delete-modal").hidden=true;await action("remove",UI.deleteIds,{delete_disk:$("delete-from-disk").checked});UI.deleteIds=[]});
 $("details-close").addEventListener("click",closeDetail);$("details-dismiss").addEventListener("click",closeDetail);
 $("details-pause").addEventListener("click",()=>UI.detailsId&&action("pause",[UI.detailsId]));
 $("details-resume").addEventListener("click",()=>UI.detailsId&&action("resume",[UI.detailsId]));
 $("details-open-folder").addEventListener("click",()=>UI.detailsId&&action("open_folder",[UI.detailsId]));
 $("connections-tab").addEventListener("click",()=>{$("connections-view").hidden=false;$("info-view").hidden=true;$("connections-tab").classList.add("selected");$("details-info-tab").classList.remove("selected")});
 $("details-info-tab").addEventListener("click",()=>{$("connections-view").hidden=true;$("info-view").hidden=false;$("details-info-tab").classList.add("selected");$("connections-tab").classList.remove("selected")});
 // Drag the floating progress window like a desktop dialog.
 const win=$("details-window");let moving=null;
 $("details-header").addEventListener("pointerdown",e=>{if(e.target.closest("button"))return;const b=win.getBoundingClientRect();moving={x:e.clientX-b.left,y:e.clientY-b.top};win.style.transform="none";win.style.left=b.left+"px";win.style.top=b.top+"px";$("details-header").setPointerCapture(e.pointerId)});
 $("details-header").addEventListener("pointermove",e=>{if(!moving)return;win.style.left=Math.max(0,Math.min(innerWidth-100,e.clientX-moving.x))+"px";win.style.top=Math.max(0,Math.min(innerHeight-35,e.clientY-moving.y))+"px"});
 $("details-header").addEventListener("pointerup",()=>moving=null);
 document.addEventListener("keydown",e=>{if(e.key==="Escape"){if(!$("details-window").hidden)closeDetail();else if(!$("download-modal").hidden)closeAdd();else{ $("options-modal").hidden=true;$("delete-modal").hidden=true}}});
}
bind();refresh();setInterval(()=>{refresh();if(UI.detailsId)refreshDetails()},1000);
