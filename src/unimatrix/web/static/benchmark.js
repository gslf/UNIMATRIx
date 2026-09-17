"use strict";
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const fmt = n => n == null ? "—" : Number(n).toLocaleString(undefined, {maximumFractionDigits: 2});
const date = s => s ? new Date(s).toLocaleString() : "—";
const domains = {D1:"Information & prediction",D2:"Trade & negotiation",D3:"Cooperative construction",D4:"Shared resources",D5:"Relationships & commitments",D6:"Delegation & allocation",D7:"Knowledge transfer",D8:"Adaptation & recovery"};
let recipes = [], models = [], runs = [], selectedRun = null, runDetail = null, episode = null, selectedAgent = "";
let view = "leaderboard", detailView = "overview", episodeId = "", messageCursor = 0, eventCursor = 0, revision = 0, refreshing = false, refreshPending = false;
let modelOriginal = {};
async function api(path, body, method = "POST") {
  const response = await fetch("/api" + path, body === undefined ? {cache:"no-store"} : {method, headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function reportError(error) { $("error").textContent = error.message; $("error").hidden = false; }
function action(id, handler, event = "click") {
  $(id).addEventListener(event, async e => {
    $("error").hidden = true;
    const button = e.currentTarget;
    if (button.tagName === "BUTTON") button.disabled = true;
    try { await handler(e); } catch (error) { reportError(error); }
    finally { if (button.tagName === "BUTTON") button.disabled = false; updateLaunch(); }
  });
}
function showView(name) {
  view = name;
  for (const item of document.querySelectorAll(".view")) item.hidden = item.id !== "view-" + name;
  for (const button of document.querySelectorAll("[data-view]")) { button.classList.toggle("active", button.dataset.view === name); button.setAttribute("aria-current", button.dataset.view === name ? "page" : "false"); }
}
function badge(status) { return node("span", status.replaceAll("_", " "), "pill " + status); }
function cell(row, value, cls) { const td = node("td", undefined, cls); td.append(value instanceof Node ? value : document.createTextNode(String(value))); row.append(td); return td; }
function button(text, handler) { const b = node("button", text); b.onclick = () => handler().catch(reportError); return b; }
function stats(target, values) { $(target).replaceChildren(...values.map(([label, value]) => { const n=node("div",undefined,"stat"); n.append(node("strong",value),node("span",label)); return n; })); }
function bars(target, title, values) {
  const container = typeof target === "string" ? $(target) : target;
  container.replaceChildren(node("h3",title));
  if (!values.length) { container.append(node("p","Scores appear after the benchmark completes.","muted")); return; }
  for (const [label,value] of values) {
    const row=node("div",undefined,"bar-row"), track=node("div",undefined,"bar-track"), fill=node("div",undefined,"bar-fill");
    fill.style.width = Math.max(0,Math.min(100,value)) + "%"; track.append(fill);
    row.append(node("span",label),track,node("b",fmt(value))); container.append(row);
  }
}
function lineChart(target, series) {
  const box=$(target); box.replaceChildren(node("h3","World resource stocks over time"));
  const keys=[...new Set(series.flatMap(s => Object.keys(s.resources)))];
  if (!series.length || !keys.length) { box.append(node("p","No resource snapshots recorded.","muted")); return; }
  const colors=["var(--accent)","#dedede","#b0b0b0","#888888","#707070"], ns="http://www.w3.org/2000/svg";
  const svg=document.createElementNS(ns,"svg"); svg.setAttribute("viewBox","0 0 900 240"); svg.classList.add("chart"); svg.setAttribute("role","img"); svg.setAttribute("aria-label","Total units of each resource by world tick");
  const max=Math.max(1,...series.flatMap(s=>Object.values(s.resources))), end=Math.max(1,series.at(-1).tick);
  for (let i=0;i<5;i++) { const y=205-i*45, line=document.createElementNS(ns,"line"), text=document.createElementNS(ns,"text");
    for (const [k,v] of Object.entries({x1:50,x2:880,y1:y,y2:y,stroke:"var(--line)"})) line.setAttribute(k,v);
    text.setAttribute("x",0);text.setAttribute("y",y+4);text.setAttribute("fill","var(--dim)");text.setAttribute("font-size","11");text.textContent=fmt(max*i/4);svg.append(line,text);
  }
  const legend=node("div",undefined,"legend");
  keys.forEach((key,i)=>{const poly=document.createElementNS(ns,"polyline");poly.setAttribute("points",series.map(s=>`${50+830*s.tick/end},${205-180*(s.resources[key]||0)/max}`).join(" "));poly.setAttribute("fill","none");poly.setAttribute("stroke",colors[i%colors.length]);poly.setAttribute("stroke-width","2");svg.append(poly);const label=node("span",key),swatch=node("i");swatch.style.background=colors[i%colors.length];label.prepend(swatch);legend.append(label);});
  const axis=document.createElementNS(ns,"text");axis.setAttribute("x",50);axis.setAttribute("y",233);axis.setAttribute("fill","var(--dim)");axis.setAttribute("font-size","11");axis.textContent=`Tick 0 → ${end} · resource units`;svg.append(axis);box.append(svg,legend);
}
function updateLaunch() {
  const recipe=recipes.find(p=>p.id===$("recipe-select").value);
  $("delete-recipe").disabled=!recipe?.deletable;
  const active=runs.some(r=>r.status==="running");
  $("start").disabled = !$("model-select").value || !recipe || recipe.archived || active;
  $("start").textContent=recipe&&!recipe.archived?`Start ${recipe.name}`:"Start benchmark";
  $("launch-recipe").textContent=recipe?.archived
    ?"Archived revision: results only. Select a current recipe to start a benchmark."
    :recipe?`${recipe.cases} episodes · selected recipe`:"Choose a benchmark recipe.";
  const selectedModel=models.find(m=>m.id===$("model-select").value)?.config;
  if(selectedModel)$("launch-recipe").textContent+=selectedModel.context_tokens
    ?` · Context: ${fmt(selectedModel.context_tokens)} tokens (prompt + reasoning + answer).`
    :" · Legacy model: edit Model settings to configure context in tokens; server defaults currently apply.";
  $("edit-model").disabled=!$("model-select").value;
  $("live-status").textContent=active?"Benchmark running":"Ready";
  $("live-status").className="pill"+(active?" running":"");
}
function renderRecipe() {
  const recipe=recipes.find(p=>p.id===$("recipe-select").value); if(!recipe) return;
  $("delete-recipe").disabled=!recipe.deletable;
  $("deletion-note").textContent=recipe.deletion_note||"";
  $("recipe-description").textContent=recipe.description;
  $("cohort-note").textContent=`Recipe revision ${recipe.recipe_hash.slice(0,10)} · Engine ${recipe.runtime.slice(0,10)}. Different revisions and compute reporting tracks are kept separate.`;
  $("recipe-title").textContent=recipe.name;$("recipe-summary").textContent=recipe.description;
  stats("recipe-stats",[["Fixed episodes",fmt(recipe.cases)],["Ticks / episode",240],["Agents / episode",8],["Generation limit","Model context"]]);
  $("recipe-json").textContent=JSON.stringify(recipe.spec,null,2);
  updateLaunch();
}
async function loadCatalog() {
  const [p,m]=await Promise.all([api("/recipes"),api("/models")]); recipes=p;models=m;
  const prior=$("recipe-select").value, model=$("model-select").value;
  $("recipe-select").replaceChildren(...recipes.map(p=>new Option(`${p.name}${p.archived?" · archived":""} · ${p.id.slice(0,8)}`,p.id)));
  $("recipe-select").value=recipes.some(p=>p.id===prior)?prior:(recipes.find(p=>p.default)||recipes[0]).id;
  $("model-select").replaceChildren(new Option("Choose a model…",""),...models.filter(m=>!m.error).map(m=>new Option(`${m.config.model} · ${m.config.snapshot} (${m.id})`,m.id)));
  $("model-select").value=models.some(m=>m.id===model)?model:"";
  const invalid=models.filter(m=>m.error); if(invalid.length) $("notice").textContent="Model files need attention: "+invalid.map(m=>m.id).join(", ");
  renderRecipe(); updateLaunch();
}
function renderRuns() {
  const visible=runs.filter(r=>r.cohort===$("recipe-select").value).sort((a,b)=>b.created_at.localeCompare(a.created_at));
  $("run-count").textContent=visible.length?`(${visible.length})`:"";$("runs-empty").hidden=!!visible.length;$("run-list").replaceChildren();
  for(const r of visible){const row=node("tr"),name=node("div",r.model);name.append(node("span",r.snapshot,"secondary"));cell(row,name);cell(row,badge(r.status));cell(row,`${r.completed_episodes}/${r.total_episodes} episodes · ${Math.round(r.completed_ticks/r.total_ticks*100)}%`);cell(row,fmt(r.report?.usi));cell(row,date(r.created_at));const actions=node("div",undefined,"controls");
    const remove=button("Delete run",()=>deleteRun(r));remove.classList.add("danger");
    remove.disabled=runs.some(run=>run.status==="running");
    remove.title=remove.disabled?"Pause the running benchmark before deleting.":"Delete this run and its recorded evidence";
    actions.append(button("Inspect",()=>openRun(r.id)),remove);cell(row,actions);$("run-list").append(row);}
}
async function deleteRun(run) {
  const path=`/benchmarks/${run.id}`,impact=await api(path+"/deletion");
  if(!confirm(`Delete run “${impact.name}” (${impact.id})? This permanently deletes this run and its ${impact.episodes} episodes, conversations and results. The recipe, model and other runs are kept.`))return;
  await api(path,{},"DELETE");revision++;
  selectedRun=null;runDetail=null;episode=null;episodeId="";
  $("detail-empty").hidden=false;$("run-detail").hidden=true;
  await loadCatalog();await refresh();
  $("notice").textContent=`Run “${impact.name}” (${impact.id}) deleted.`;
}
async function refresh() {
  if(refreshing) {refreshPending=true;return;} refreshing=true;const token=revision;
  try {
    const [all, ranking]=await Promise.all([api("/benchmarks"),api(`/leaderboard?cohort=${$("recipe-select").value}&track=${$("track").value}`)]);
    if(token!==revision)return;runs=all;renderRuns();updateLaunch();
    $("ranking").replaceChildren();$("ranking-empty").hidden=!!ranking.length;
    ranking.forEach((r,i)=>{const row=node("tr"),name=node("div",r.model);name.append(node("span",r.snapshot,"secondary"));cell(row,i+1);cell(row,name);cell(row,fmt(r.report.usi),"score");cell(row,r.report.ci95.map(fmt).join(" – "));cell(row,r.total_episodes);cell(row,date(r.updated_at));cell(row,button("Inspect run",()=>openRun(r.id)));$("ranking").append(row);});
    $("comparison-chart").hidden=!ranking.length;bars("comparison-chart","Completed competitors",ranking.map(r=>[r.model, r.report.usi]));
    if(selectedRun&&view==="details")await loadRun(token);
  } finally {refreshing=false;if(refreshPending){refreshPending=false;await refresh();}}
}
async function openRun(id){revision++;selectedRun=id;episodeId="";episode=null;selectedAgent="";detailView="overview";showView("details");await loadRun(revision);}
async function loadRun(token=revision){
  const result=await api("/benchmarks/"+selectedRun);if(token!==revision)return;runDetail=result;
  $("detail-empty").hidden=true;$("run-detail").hidden=false;
  $("detail-title").textContent=result.model;$("detail-recipe").textContent=result.recipe_name;
  $("detail-meta").textContent=`${result.snapshot} · ${result.status} · parallel requests: ${result.parallelism??1} · active episodes: ${result.current_episodes?.length??(result.current_episode?1:0)} · ${result.id} · ${date(result.created_at)}`;
  $("run-progress").max=result.total_ticks;$("run-progress").value=result.completed_ticks;
  $("run-failure").hidden=!result.error;$("run-failure").textContent=result.error||"";
  $("pause").hidden=result.status!=="running";$("resume").hidden=!["paused","interrupted","failed"].includes(result.status);
  $("export").hidden=result.status!=="completed";$("export").href=`/api/benchmarks/${result.id}/export`;
  stats("run-stats",[["Benchmark score",fmt(result.report?.usi)],["95% interval",result.report?result.report.ci95.map(fmt).join(" – "):"Pending"],["Episodes completed",`${result.completed_episodes} / ${result.total_episodes}`],["Progress",Math.round(result.completed_ticks/result.total_ticks*100)+"%"],["Recorded messages",fmt(result.telemetry?.messages)],["Generated tokens reported",fmt(result.telemetry?.reported_generated_tokens)]]);
  $("candidate-json").textContent=JSON.stringify(result.candidate,null,2);
  bars("domain-chart","Results by domain",Object.entries(result.report?.domains||{}).map(([k,v])=>[`${k} · ${domains[k]}`,v.score]));
  $("strata").replaceChildren();for(const [name,values]of Object.entries(result.report?.strata||{})){const card=node("div",undefined,"card");bars(card,"Score by "+name,Object.entries(values));$("strata").append(card);}
  const previous=$("domain-filter").value;$("domain-filter").replaceChildren(new Option("All domains",""),...[...new Set(result.episodes.map(e=>e.domain))].map(d=>new Option(d+" · "+domains[d],d)));$("domain-filter").value=previous;
  renderEpisodes();
  const started=result.episodes.filter(e=>e.status!=="pending");$("episode-select").replaceChildren(...started.map(e=>new Option(`#${e.index} · ${e.domain} · seed ${e.seed} · ${e.role}`,e.run_id)));
  if(!started.some(e=>e.run_id===episodeId))episodeId=started[0]?.run_id||"";$("episode-select").value=episodeId;
  await showDetail(detailView,false);
}
function renderEpisodes(){
  $("episodes").replaceChildren();for(const e of runDetail.episodes.filter(e=>!$("domain-filter").value||e.domain===$("domain-filter").value)){const row=node("tr");cell(row,`#${e.index}`);cell(row,e.domain);cell(row,e.seed);cell(row,e.role);cell(row,`${e.completed_tick}/240 · ${e.status}`);cell(row,fmt(e.score));const b=button("Explore",async()=>{episodeId=e.run_id;$("episode-select").value=episodeId;episode=null;await showDetail("agents");});b.disabled=e.status==="pending";cell(row,b);$("episodes").append(row);}
}
async function showDetail(name, reload=true){
  detailView=name;for(const key of ["overview","episodes","agents","messages","events"])$("detail-"+key).hidden=key!==name;
  for(const b of document.querySelectorAll("[data-detail]"))b.classList.toggle("active",b.dataset.detail===name);
  const inspect=["agents","messages","events"].includes(name);$("episode-toolbar").hidden=!inspect;$("episode-metrics").hidden=name!=="agents";
  if(!inspect||!selectedRun)return;
  if(!episodeId){$("episode-status").textContent="No episode has started yet.";return;}
  if(!reload&&episode&&episode.manifest.run_id===episodeId)return;
  if(reload||!episode||episode.manifest.run_id!==episodeId)await loadEpisode();
  if(name==="messages")await loadMessages(false);
  if(name==="events")await loadEvents(false);
}
function epPath(){return `/benchmarks/${selectedRun}/episodes/${episodeId}`;}
async function loadEpisode(){
  const path=epPath(),token=revision,result=await api(path);if(token!==revision||path!==epPath())return;episode=result;
  $("episode-status").textContent=`${result.status} · ${result.completed_tick}/240 ticks`;
  $("world-tick").max=result.completed_tick;$("world-tick").value=result.completed_tick;$("world-tick-value").textContent=result.completed_tick;
  $("decision-tick").max=Math.max(0,result.completed_tick-1);$("decision-tick").value=Math.max(0,result.completed_tick-1);
  $("decision").replaceChildren();renderAgents(result.state);lineChart("resource-chart",result.series);
  const participant=$("message-agent").value;$("message-agent").replaceChildren(new Option("All agents",""),...Object.keys(result.state.agents).map(s=>new Option(agentName(s),s)));$("message-agent").value=participant;
  $("episode-metrics").replaceChildren();for(const [name,m]of Object.entries(result.metrics)){const card=node("div",undefined,"card"),details=node("details"),pre=node("pre",JSON.stringify(m,null,2));card.append(node("h3",name),node("strong",fmt(m.normalized_value*100),"score"));details.append(node("summary","Metric evidence"),pre);card.append(details);$("episode-metrics").append(card);}
}
function agentName(slot){return slot+(slot===episode?.manifest.focal_slot?" · candidate":"");}
function renderAgents(state){
  if(!state.agents[selectedAgent])selectedAgent=episode.manifest.focal_slot;
  $("agents").replaceChildren();for(const [slot,agent]of Object.entries(state.agents)){const b=node("button",agentName(slot),"agent-button"+(slot===selectedAgent?" selected":""));b.append(node("span",agent.alive?"active":"inactive"));b.onclick=()=>{selectedAgent=slot;$("decision").replaceChildren();renderAgents(state);};$("agents").append(b);}
  $("agent-title").textContent=agentName(selectedAgent);const agent=state.agents[selectedAgent];$("agent-state").replaceChildren();
  for(const [key,value]of Object.entries(agent)){const row=node("div",undefined,"data-row");row.append(node("span",key),node("div",typeof value==="object"?JSON.stringify(value):String(value)));$("agent-state").append(row);}
}
async function loadMessages(append){
  if(!episodeId)return;const path=epPath(),token=revision;
  const data=await api(path+`/events?kind=messages&agent=${encodeURIComponent($("message-agent").value)}&after=${append?messageCursor:0}`);
  if(token!==revision||path!==epPath())return;messageCursor=data.cursor;if(!append)$("messages").replaceChildren();
  for(const m of data.items){const box=node("article",undefined,"message card");box.append(node("div",`Tick ${m.tick} · ${agentName(m.actor_id)} → ${(m.payload.to||[]).length===episode.manifest.slots.length?"all agents":(m.payload.to||[]).join(", ")}${m.payload.channel?" · "+m.payload.channel:""}`,"message-meta"),node("p",m.payload.content||""));$("messages").append(box);}
  if(!$("messages").children.length)$("messages").append(node("div","No recorded messages for this episode and participant.","empty card"));$("more-messages").hidden=!data.more;
}
async function loadEvents(append){
  if(!episodeId)return;const path=epPath(),token=revision,data=await api(path+`/events?after=${append?eventCursor:0}`);
  if(token!==revision||path!==epPath())return;eventCursor=data.cursor;if(!append)$("events").replaceChildren();
  for(const e of data.items){const d=node("details");d.append(node("summary",`Tick ${e.tick} · ${e.type.replaceAll("_"," ")} · ${e.actor_id||"world"} · ${e.visibility.scope}`),node("pre",JSON.stringify(e.payload,null,2)));$("events").append(d);}
  if(!$("events").children.length)$("events").append(node("p","No recorded events yet.","muted"));$("more-events").hidden=!data.more;
}
function fillModel(id,config={}){
  modelOriginal=structuredClone(config);$("model-id").value=id;$("model-name").value=config.model||"";$("model-snapshot").value=config.snapshot||"";
  const url=new URL(config.endpoint||"http://localhost:1234");$("model-port").value=url.port;url.port="";$("model-address").value=url.href.replace(/\/$/,"");
  $("model-context").value=config.context_tokens??"";$("model-track").value=config.budget_track||"opaque_compute";
  for(const [id,key]of [["temperature","temperature"],["seed","seed"],["input-price","input_price_per_million"],["output-price","output_price_per_million"],["max-input","max_input_tokens"],["key-env","api_key_env"],["key-file","api_key_file"]])$("model-"+id).value=config[key]??(key==="temperature"?0:"");
  $("model-key").value="";$("model-remove-key").checked=false;$("model-error").textContent="";
}
function openModel(fresh=false){const m=models.find(m=>m.id===$("model-select").value);fillModel(fresh?"":m?.id||"",fresh?{}:m?.config||{});$("model-dialog").showModal();}
action("add-model",()=>openModel(true));action("edit-model",()=>openModel());action("close-model",()=>$("model-dialog").close());
action("model-select",()=>{const m=models.find(m=>m.id===$("model-select").value);if(m)$("track").value=m.config.budget_track;return refresh();},"change");
$("model-form").addEventListener("submit",async e=>{
  e.preventDefault();$("model-error").textContent="";$("save-model").disabled=true;
  try{const config={...modelOriginal},url=new URL($("model-address").value);if($("model-port").value)url.port=$("model-port").value;
    delete config.context_bytes_verified;
    Object.assign(config,{model:$("model-name").value.trim(),snapshot:$("model-snapshot").value.trim(),endpoint:url.href.replace(/\/$/,""),context_tokens:Number($("model-context").value),budget_track:$("model-track").value});
    for(const [id,key]of [["temperature","temperature"],["seed","seed"],["input-price","input_price_per_million"],["output-price","output_price_per_million"],["max-input","max_input_tokens"]]){delete config[key];if($("model-"+id).value!=="")config[key]=Number($("model-"+id).value);}
    for(const [id,key]of [["key-env","api_key_env"],["key-file","api_key_file"]]){delete config[key];if($("model-"+id).value.trim())config[key]=$("model-"+id).value.trim();}
    const body={config};if($("model-remove-key").checked)body.api_key="";else if($("model-key").value)body.api_key=$("model-key").value;
    const id=$("model-id").value;await api("/models/"+encodeURIComponent(id),body,"PUT");$("model-dialog").close();await loadCatalog();$("model-select").value=id;$("track").value=config.budget_track;$("notice").textContent="Model configuration saved.";updateLaunch();await refresh();
  }catch(error){$("model-error").textContent=error.message;}finally{$("save-model").disabled=false;}
});
action("import-model",async()=>{try{const file=$("import-model").files[0];if(!file)return;const value=JSON.parse(await file.text());if(!value.model||!value.endpoint)throw new Error("Choose a candidate model JSON, not a run or recipe file.");fillModel(file.name.replace(/\.json$/i,"").replace(/[^A-Za-z0-9_-]/g,"-"),value);}catch(error){$("model-error").textContent=error.message;}},"change");
action("start",async()=>{const recipe=recipes.find(p=>p.id===$("recipe-select").value);if(!recipe||recipe.archived)throw new Error("Select a current benchmark recipe first.");const r=await api("/benchmarks",{model_id:$("model-select").value,recipe_id:recipe.recipe_id,cohort:recipe.id,parallelism:Number($("parallelism").value)});await loadCatalog();$("recipe-select").value=r.cohort;renderRecipe();$("notice").textContent=`Started ${r.model} on ${r.recipe_name}.`;await openRun(r.id);await refresh();});
action("pause",async()=>{await api(`/benchmarks/${selectedRun}/pause`,{});await refresh();});
action("resume",async()=>{await api(`/benchmarks/${selectedRun}/resume`,{});await refresh();});
action("refresh",async()=>{await loadCatalog();await refresh();});
action("delete-recipe",async()=>{
  const recipe=recipes.find(p=>p.id===$("recipe-select").value);if(!recipe?.deletable)return;
  const path="/recipe-lab/recipes/"+encodeURIComponent(recipe.recipe_id);
  const impact=await api(path+"/deletion");
  if(!confirm(`Delete recipe “${impact.name}”? This permanently deletes all its saved revisions, ${impact.drafts} drafts (including holdout), ${impact.evaluations} evaluations and ${impact.benchmarks} benchmark runs, with their episodes, conversations and results.`))return;
  revision++;await api(path,{},"DELETE");
  selectedRun=null;runDetail=null;episode=null;episodeId="";
  $("detail-empty").hidden=false;$("run-detail").hidden=true;
  await loadCatalog();await refresh();
  $("notice").textContent=`Recipe “${impact.name}” and its associated evaluations deleted.`;
});
action("recipe-select",async()=>{revision++;selectedRun=null;runDetail=null;episode=null;episodeId="";$("detail-empty").hidden=false;$("run-detail").hidden=true;renderRecipe();renderRuns();$("ranking").replaceChildren();$("ranking-empty").hidden=true;$("comparison-chart").hidden=true;await refresh();},"change");
action("track",async()=>{revision++;$("ranking").replaceChildren();$("ranking-empty").hidden=true;$("comparison-chart").hidden=true;await refresh();},"change");action("domain-filter",renderEpisodes,"change");
action("episode-select",async()=>{revision++;episodeId=$("episode-select").value;episode=null;await showDetail(detailView);},"change");
action("message-agent",()=>loadMessages(false),"change");action("more-messages",()=>loadMessages(true));action("more-events",()=>loadEvents(true));
action("world-tick",async()=>{const tick=Number($("world-tick").value),state=await api(epPath()+`/state?tick=${tick}`);$("world-tick-value").textContent=tick;renderAgents(state);},"change");
action("load-decision",async()=>{if(!episodeId||!selectedAgent)return;$("decision").replaceChildren();const data=await api(epPath()+`/decision?agent=${encodeURIComponent(selectedAgent)}&tick=${$("decision-tick").value}`);for(const [label,value]of [["Saved observation",data.observation],["Model / policy response",data.response],["Inference calls",data.calls]]){const d=node("details");d.open=label==="Model / policy response";d.append(node("summary",label),node("pre",typeof value==="string"?value:JSON.stringify(value,null,2)));$("decision").append(d);}});
for(const b of document.querySelectorAll("[data-view]"))b.onclick=()=>{showView(b.dataset.view);if(selectedRun&&view==="details")loadRun().catch(reportError);};
for(const b of document.querySelectorAll("[data-detail]"))b.onclick=()=>showDetail(b.dataset.detail).catch(reportError);
(async()=>{await loadCatalog();await refresh();})().catch(reportError);
setInterval(()=>{if(!document.hidden&&!$("model-dialog").open&&runs.some(r=>r.status==="running"))refresh().catch(reportError);},4000);
