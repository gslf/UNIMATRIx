"use strict";
const $=id=>document.getElementById(id), el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const format=n=>n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:2});
const variant=v=>({normal:"Normal",no_memory:"No memory",no_communication:"No communication",reverse_observation_order:"Reverse peer order",rename_agents:"Rename agents"}[v]||v);
const ident=location.pathname.split("/").filter(Boolean).at(-1), base="/api/recipe-lab/evaluations/"+encodeURIComponent(ident);
const query=new URLSearchParams(location.search),PANELS=["activity","agents","metrics","calls","messages","events"];
let overview=null,studyId=query.get("study"),episodeId=query.get("episode"),locate=episodeId,detail=null,offset=0,panel=PANELS.includes(query.get("panel"))?query.get("panel"):"activity",revision=0,paneRevision=0,busy=false;
let eventCursor=0,messageCursor=0,callOffset=0,signature="";
if(studyId!==null)$("follow").checked=false;
async function api(path,method="GET"){const r=await fetch(base+path,{method,cache:"no-store"});const v=await r.json();if(!r.ok)throw new Error(typeof v.detail==="string"?v.detail:JSON.stringify(v.detail));return v;}
function agentLabel(slot,focal=detail?.focal_slot){const label=String(slot??"world").replace(/^slot-(\d+)$/,"AGENT $1");return label+(slot===focal?" - Protagonist":"");}
function fail(e){$("error").textContent=e.message;$("error").hidden=false;}
function warning(id,text){$(id).textContent=text||"";$(id).hidden=!text;}
function on(id,fn,event="click"){$(id).addEventListener(event,()=>Promise.resolve().then(fn).catch(fail));}
function epPath(){return `/studies/${encodeURIComponent(studyId)}/episodes/${encodeURIComponent(episodeId)}`;}
function stats(rows){$("episode-stats").replaceChildren(...rows.map(([title,value])=>{const n=el("div",undefined,"stat");n.append(el("strong",value),el("span",title));return n;}));}
function pretty(value){if(typeof value==="string"){try{return JSON.stringify(JSON.parse(value),null,2);}catch{return value;}}return JSON.stringify(value,null,2);}
function block(title,value,open=false){const d=el("details",undefined,"evidence-record");d.open=open;d.append(el("summary",title),el("pre",pretty(value)));return d;}
function selectStudy(id){$("follow").checked=false;studyId=id;episodeId=null;offset=0;signature="";refresh().catch(fail);}
function selectEpisode(id){$("follow").checked=false;episodeId=id;signature="";refresh().catch(fail);}
function renderOverview(data){
  overview=data;$("evaluation-name").textContent=data.name;$("recipe-name").textContent=`${data.recipe_name} · ${data.split}`;
  $("evaluation-status").textContent=data.status.replaceAll("_"," ");$("phase").textContent=data.phase;
  warning("evaluation-error",data.error);$("pause").hidden=data.status!=="running";$("resume").hidden=!["paused","interrupted"].includes(data.status);
  const active=data.studies.find(s=>s.status==="running");
  const stage=data.status.startsWith("completed")?3:data.phase==="Analyzing evaluation"?2:active||data.studies.some(s=>s.completed_episodes>0||s.current?.completed_tick>0)?1:0;
  $("stages").replaceChildren(...["1 · Queued","2 · Episodes & study scores","3 · Analysis","4 · Results"].map((text,i)=>el("span",text,"stage"+(i===stage?" active":""))));
  if(!data.studies.some(s=>s.id===studyId)||($("follow").checked&&active)){
    const chosen=active||data.studies.find(s=>s.status==="failed")||data.studies[0];
    if(chosen?.id!==studyId){studyId=chosen?.id;episodeId=null;offset=0;signature="";}
  }
  $("study-list").replaceChildren(...data.studies.map((s,i)=>{
    const b=el("button",undefined,"study-card"+(s.id===studyId?" selected":""));b.setAttribute("aria-pressed",String(s.id===studyId));
    b.append(el("strong",`${i+1}. ${s.label}`),el("span",`${s.candidate.kind==="scripted"?"Scripted baseline":"Model"} · ${variant(s.intervention)}`),el("span",`${s.phase} · ${s.completed_episodes}/${s.total_episodes} episodes · ${s.current_episodes?.length??0} in progress · parallel requests: ${data.parallelism??1}`));
    const p=el("progress");p.max=s.total_episodes*(s.ticks_per_episode??240);p.value=s.completed_episodes*(s.ticks_per_episode??240)+(s.current_ticks??s.current?.completed_tick??0);p.setAttribute("aria-label",s.label+" progress");b.append(p);
    if(s.score!=null)b.append(el("span",`Score ${format(s.score)} / 100`));b.onclick=()=>selectStudy(s.id);return b;
  }));
  const s=data.studies.find(s=>s.id===studyId);if(!s)return;
  $("study-name").textContent=s.label;$("study-variant").textContent=variant(s.intervention);warning("study-error",s.error);
  $("study-model").textContent=s.candidate.kind==="model"?`${s.candidate.name} · ${s.candidate.snapshot} · ${s.candidate.endpoint} · Context: ${s.candidate.context_tokens?format(s.candidate.context_tokens)+" tokens":"provider settings"}`:`Scripted baseline: ${s.candidate.name}. ${s.provider_agents?`${s.provider_agents} model peers may call their providers.`:"All agents use local rules; no provider requests."}`;
}
async function refresh(poll=false){
  if(poll&&busy)return;busy=true;const token=++revision;paneRevision++;$("error").hidden=true;
  try{
    const data=await api("/explorer");if(token!==revision)return;renderOverview(data);if(studyId==null)return;
    let rows=await api(`/studies/${studyId}/episodes?offset=${offset}${locate?"&locate="+encodeURIComponent(locate):""}`);if(token!==revision)return;offset=rows.offset;locate=null;
    if($("follow").checked&&rows.current_index!=null&&Math.floor(rows.current_index/48)*48!==offset){offset=Math.floor(rows.current_index/48)*48;rows=await api(`/studies/${studyId}/episodes?offset=${offset}`);if(token!==revision)return;}
    const s=data.studies.find(s=>s.id===studyId);
    if($("follow").checked&&s.current_episode)episodeId=s.current_episode;
    if(!rows.items.some(e=>e.id===episodeId))episodeId=(rows.items.find(e=>e.status==="running")||rows.items.find(e=>e.status!=="pending")||rows.items[0])?.id;
    $("episode-page").textContent=`${rows.total?offset+1:0}–${Math.min(rows.total,offset+48)} of ${rows.total}`;$("previous-page").disabled=offset===0;$("next-page").disabled=offset+48>=rows.total;
    $("episode-list").replaceChildren(...rows.items.map(e=>{
      const b=el("button",undefined,`episode-card ${e.status}`+(e.id===episodeId?" selected":""));b.setAttribute("aria-pressed",String(e.id===episodeId));b.append(el("strong",`#${e.index} · ${e.domain}`),el("span",`Seed ${e.seed} · ${e.role} · replicate ${e.replicate} · ${e.complexity}`),el("span",`${e.status.replaceAll("_"," ")} · ${e.completed_tick}/${e.ticks} ticks`));b.onclick=()=>selectEpisode(e.id);return b;
    }));
    if(!episodeId)return;
    const d=await api(epPath());if(token!==revision)return;
    const changed=detail?.id!==d.id;detail=d;
    const episode=rows.items.find(e=>e.id===episodeId);$("episode-name").textContent=`Episode #${episode?.index||""} · ${episode?.domain||""}`;
    $("episode-phase").textContent=`${d.phase} · ${d.completed_tick}/${d.ticks} world ticks committed`;
    $("episode-status").textContent=d.status.replaceAll("_"," ");$("tick-progress").max=d.ticks;$("tick-progress").value=d.completed_tick;
    stats([["Recorded provider attempts",format(d.totals.provider_attempts||0)],["Reported output tokens",format(d.totals.output_tokens)],["Mean attempt latency (s)",format(d.totals.mean_latency)],["Recorded provider errors",format(d.totals.errors||0)]]);
    const waitingModels=d.waiting.filter(s=>d.policies[s]?.kind==="model");
    $("waiting").textContent=d.status==="running"?`${d.saved_decisions}/${d.agents.length-(d.authorized_waiting?.length??0)} decisions saved for tick ${d.completed_tick}. ${d.authorized_waiting?.length?d.authorized_waiting.length+" agents chose to wait. ":""}${waitingModels.length?"Awaiting model decisions: "+waitingModels.map(s=>agentLabel(s)).join(", ")+".":""} Awaiting decisions is inferred from saved evidence; it does not confirm an HTTP request is in flight.`:"";
    $("pending").hidden=d.status!=="pending";$("evidence").hidden=d.status==="pending";
    const agent=$("agent").value;$("agent").replaceChildren(...Object.entries(d.policies).map(([s,p])=>new Option(`${agentLabel(s,d.focal_slot)} · ${p.name}${p.personality?" · "+p.personality:""}`,s)));$("agent").value=!changed&&d.policies[agent]?agent:d.focal_slot;
    $("decision-tick").max=d.ticks-1;
    if(changed||$("follow").checked)$("decision-tick").value=Math.min(d.ticks-1,d.last_decision_tick??d.completed_tick);
    renderActivity(d);
    const nextSignature=[d.id,d.completed_tick,d.last_decision_tick,d.totals.provider_attempts,panel,$("agent").value,$("decision-tick").value].join(":");
    if(d.status!=="pending"&&(changed||!poll||($("follow").checked&&signature!==nextSignature))){signature=nextSignature;await loadPanel(true);}
    $("updated").textContent="Updated "+new Date().toLocaleTimeString();
  }catch(e){if(token===revision)fail(e);}finally{if(token===revision)busy=false;}
}
function renderActivity(d){
  const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 900 180");svg.setAttribute("role","img");svg.setAttribute("aria-label","Recorded events, messages and rejections by tick");
  const max=Math.max(1,...d.series.map(s=>s.events)),plotTicks=Math.max(8,d.completed_tick);
  for(const row of d.series){for(const [key,color]of [["events","#888888"],["messages","#dedede"],["rejected","var(--accent)"]]){const rect=document.createElementNS(ns,"rect"),height=145*row[key]/max;for(const [k,v]of Object.entries({x:20+row.tick/plotTicks*860,y:155-height,width:Math.max(2,860/plotTicks-1),height,fill:color}))rect.setAttribute(k,String(v));const title=document.createElementNS(ns,"title");title.textContent=`Tick ${row.tick}: ${row[key]} ${key}`;rect.append(title);rect.onclick=()=>inspectTick(Math.max(0,row.tick-1));svg.append(rect);}}
  for(const [x,text]of [[20,"Tick 0"],[740,`Tick ${plotTicks}`]]){const label=document.createElementNS(ns,"text");label.setAttribute("x",x);label.setAttribute("y",175);label.setAttribute("fill","var(--dim)");label.textContent=text;svg.append(label);}
  $("activity-chart").replaceChildren(d.series.length?svg:el("p","No committed events yet.","muted"));
  $("world-phases").replaceChildren(...d.phases.map(p=>el("span",`${p.phase} · ${p.events} events`,"stage")));
  $("agent-status").replaceChildren(...Object.entries(d.policies).map(([slot,p])=>{const b=el("button",undefined,"agent-tile");b.append(el("strong",agentLabel(slot,d.focal_slot)),el("span",p.kind==="model"?p.name+(p.personality?" · "+p.personality:""):`Scripted · ${p.name}${p.personality?" · "+p.personality:""}`),el("span",d.status==="running"?(!d.agents.includes(slot)?"Inactive":d.authorized_waiting?.includes(slot)?"Waiting by choice":d.waiting.includes(slot)?"Decision not saved yet":"Decision saved"):d.status.replaceAll("_"," ")));b.onclick=()=>{$("agent").value=slot;showPanel("agents");};return b;}));
}
function inspectTick(tick){$("follow").checked=false;$("decision-tick").value=Math.min(tick,detail.ticks-1);showPanel("agents");}
function applyPanel(){for(const n of PANELS)$("panel-"+n).hidden=n!==panel;for(const b of document.querySelectorAll("[data-panel]")){b.classList.toggle("active",b.dataset.panel===panel);b.setAttribute("aria-current",b.dataset.panel===panel?"page":"false");}}
function showPanel(name){panel=name;signature="";applyPanel();loadPanel(true).catch(fail);}
async function loadPanel(reset){
  if(!detail||detail.status==="pending")return;
  const token=++paneRevision,path=epPath(),version=revision,chosen=panel;
  const current=()=>token===paneRevision&&version===revision&&path===epPath()&&chosen===panel;
  if(panel==="agents"){
    const tick=Number($("decision-tick").value);if(!Number.isInteger(tick)||tick<0||tick>=detail.ticks)throw new Error("Choose a decision tick within this episode.");
    const [decision,state]=await Promise.all([api(path+`/decision?agent=${encodeURIComponent($("agent").value)}&tick=${tick}`),api(path+`/state?tick=${tick}`).catch(e=>({unavailable:e.message}))]);if(!current())return;
    $("decision-note").textContent=decision.recorded?`Saved decision at tick ${tick}. World state is before that decision.`:decision.authorized_wait?`The agent chose to wait at tick ${decision.authorized_wait.origin_tick}, until tick ${decision.authorized_wait.until_tick} or new information. No response was requested at tick ${tick}.`:"No decision saved for this agent at this tick. It may be pending, failed or not scheduled.";
    const agentState=state.agents?.[$("agent").value];
    $("agent-snapshot").replaceChildren();
    if(agentState){$("agent-snapshot").append(el("h3",`Agent state · tick ${tick}`),el("p",`${agentState.alive?"Alive":"Inactive"} · location: ${agentState.location}`));for(const [resource,amount]of Object.entries(agentState.inventory||{})){const row=el("div",undefined,"data-row");row.append(el("span",resource),el("strong",format(amount/1000)+" units"));$("agent-snapshot").append(row);}}
    $("world-state").textContent=pretty(state);$("decision-content").replaceChildren();
    if(decision.recorded){const original=decision.calls.find(c=>c.research_input);$("decision-content").append(...(detail.policies[$("agent").value].system_prompt?[block("Peer personality",detail.policies[$("agent").value].system_prompt)]:[]),block("Observation from the world",decision.observation),block(detail.policies[$("agent").value].kind==="model"?"Input actually sent to the model":"Input to scripted agent",original?.research_input||decision.observation),block(detail.policies[$("agent").value].kind==="model"?"Raw model output":"Scripted output",original?.research_output??decision.response,true),block("Response submitted to the engine",decision.response),block("Recorded attempt details",decision.calls));}
  }else if(panel==="metrics"){
    const data=await api(path+"/metrics").catch(e=>({unavailable:e.message}));if(!current())return;
    $("metric-list").replaceChildren();
    if(data.unavailable){$("metric-list").append(el("p",data.unavailable,"muted"));return;}
    $("metric-list").append(el("p",`Episode score ${format(100*data.score)} / 100 for ${agentLabel(data.focal)}. Each metric lists the events that produced it; click one to inspect the decisions of that tick.`,"muted"));
    for(const m of data.metrics){
      const card=el("details",undefined,"evidence-record"),points=m.weight==null?"":` · weight ${format(m.weight)} → ${format(100*m.value*m.weight)} points`;card.open=m.value<0.98;
      card.append(el("summary",`${m.metric} · ${format(100*m.value)} / 100${points} · ${format(m.numerator)} of ${m.denominator}`));
      const many=m.contributions.length>24,shown=many?m.lost:m.contributions;
      if(many)card.append(el("p",`${m.contributions.length} scoring events; the five that lost the most are shown.`,"muted small"));
      for(const c of shown){const b=el("button",`Tick ${c.tick} · ${c.label} · ${format(100*c.value)} · event #${c.seq}`,"agent-button");b.onclick=()=>inspectTick(Math.max(0,c.tick-1));card.append(b);}
      $("metric-list").append(card);
    }
  }else if(panel==="calls"){
    const start=reset?0:callOffset;const data=await api(path+`/calls?offset=${start}`);if(!current())return;
    if(reset)$("call-list").replaceChildren();for(const call of data.items){const row=block(`${agentLabel(call.slot)} · tick ${call.tick??"unknown (no saved decision)"} · attempt ${call.attempt} · ${format(call.latency)} s${call.error?" · ERROR":""}`,call);if(call.error)row.classList.add("error");$("call-list").append(row);}callOffset=start+data.items.length;$("more-calls").hidden=!data.more;if(!callOffset)$("call-list").append(el("p","No completed provider attempts recorded. Scripted decisions do not appear here.","muted"));
  }else if(panel==="events"||panel==="messages"){
    const messages=panel==="messages",start=reset?0:messages?messageCursor:eventCursor,kind=messages?"messages":$("event-filter").value;
    const data=await api(path+`/events?after=${start}&kind=${kind}`);if(!current())return;
    const target=$(messages?"message-list":"event-list");if(reset)target.replaceChildren();
    for(const event of data.items){if(messages){const row=el("article",undefined,"evidence-record");row.append(el("strong",`Tick ${event.tick} · ${agentLabel(event.actor_id)} → ${(event.payload.to||[]).map(s=>agentLabel(s)).join(", ")||event.payload.channel||"public"}`),el("p",event.payload.content||"","message-text"));row.append(block("Message event",event));target.append(row);}else target.append(block(`#${event.seq} · tick ${event.tick} · ${event.phase} · ${event.type} · ${agentLabel(event.actor_id||"world")}`,event));}
    if(messages)messageCursor=data.cursor;else eventCursor=data.cursor;$(messages?"more-messages":"more-events").hidden=!data.more;if(!target.children.length)target.append(el("p","No matching events recorded yet.","muted"));
  }
}
for(const b of document.querySelectorAll("[data-panel]"))b.onclick=()=>showPanel(b.dataset.panel);
on("refresh",()=>refresh());on("follow",()=>refresh(),"change");
on("pause",async()=>{await api("/pause","POST");await refresh();});on("resume",async()=>{await api("/resume","POST");await refresh();});
on("previous-page",()=>{$("follow").checked=false;offset=Math.max(0,offset-48);episodeId=null;return refresh();});
on("next-page",()=>{$("follow").checked=false;offset+=48;episodeId=null;return refresh();});
on("agent",()=>{$("follow").checked=false;return loadPanel(true);},"change");on("load-decision",()=>{$("follow").checked=false;return loadPanel(true);});
on("latest-decision",()=>{$("decision-tick").value=detail.last_decision_tick??0;return loadPanel(true);});
for(const id of ["more-calls","more-messages","more-events"])on(id,()=>{$("follow").checked=false;return loadPanel(false);});on("event-filter",()=>loadPanel(true),"change");
applyPanel();refresh();setInterval(()=>{if(!document.hidden&&overview?.status==="running")refresh(true);},3000);
