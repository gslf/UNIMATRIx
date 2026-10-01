"use strict";
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const fmt = n => n == null ? "—" : Number(n).toLocaleString(undefined, {maximumFractionDigits: 4});
const date = s => s ? new Date(s).toLocaleString() : "—";
const domains = {D1:"Information & prediction",D2:"Trade & negotiation",D3:"Cooperative construction",D4:"Shared resources",D5:"Relationships & commitments",D6:"Delegation & allocation",D7:"Knowledge transfer",D8:"Adaptation & recovery"};
let recipes = [], models = [], runs = [], selectedRun = null, runDetail = null, episode = null, selectedAgent = "";
let personalities={};
let view = "leaderboard", detailView = "overview", episodeId = "", messageCursor = 0, eventCursor = 0, revision = 0, refreshing = false, refreshPending = false;
let modelOriginal = {};
let rankingSignature = "";
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
  const low=Math.min(0,...values.map(v=>v[1])),high=Math.max(0,...values.map(v=>v[1])),span=high-low||1;
  for (const [label,value] of values) {
    const row=node("div",undefined,"bar-row"), track=node("div",undefined,"bar-track"), fill=node("div",undefined,"bar-fill");
    track.style.position="relative";fill.style.position="absolute";fill.style.left=((Math.min(0,value)-low)/span*100)+"%";fill.style.width=(Math.abs(value)/span*100)+"%";track.append(fill);
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
  const active=runs.some(r=>r.status==="running");
  $("start").disabled = !$("model-select").value || !recipe || recipe.archived || active;
  $("start").textContent=recipe&&!recipe.archived?`Start ${recipe.name}`:"Start benchmark";
  $("launch-recipe").textContent=recipe?.archived
    ?"Archived revision: results only. Select a current recipe to start a benchmark."
    :recipe?`${recipe.cases} episodes · selected recipe`:"Choose a benchmark recipe.";
  const selectedModel=models.find(m=>m.id===$("model-select").value)?.config;
  if(selectedModel)$("launch-recipe").textContent+=selectedModel.context_tokens
    ?` · Context: ${fmt(selectedModel.context_tokens)} tokens (prompt + reasoning + answer).`
    :" · OpenAI-compatible endpoint: the provider's context settings apply.";
  $("edit-model").disabled=!$("model-select").value;
  $("live-status").textContent=active?"Benchmark running":"Ready";
  $("live-status").className="pill"+(active?" running":"");
}
function renderRecipe() {
  $("stability-result").replaceChildren();
  const recipe=recipes.find(p=>p.id===$("recipe-select").value); if(!recipe) return;
  $("cohort-note").textContent=`Recipe revision ${recipe.recipe_hash.slice(0,10)} · Engine ${recipe.runtime.slice(0,10)}. Different revisions and compute reporting tracks are kept separate.`;
  $("recipe-title").textContent=recipe.name;$("recipe-summary").textContent=recipe.description;
  stats("recipe-stats",[["Fixed episodes",fmt(recipe.cases)],["Ticks / episode",recipe.spec?.ticks??240],["Agents / episode",(recipe.spec?.peers?.length??7)+1],["Generation limit","Model context"],["Candidate decisions",fmt(recipe.cases*(recipe.spec?.ticks??240))],["Time budget",recipe.spec?.max_wall_seconds?`${recipe.spec.max_wall_seconds/3600} h`:"Custom"]]);
  const priorProfile=$("candidate-profile").value, profiles=recipe.spec?.candidate_profiles||{};
  $("candidate-profile").replaceChildren(new Option("Saved model personality",""),...Object.entries(profiles).map(([id,p])=>new Option(p.name,id)));
  $("candidate-profile").value=priorProfile in profiles?priorProfile:"";
  $("profile-label").hidden=!Object.keys(profiles).length;
  $("recipe-json").textContent=JSON.stringify(recipe.spec,null,2);
  updateLaunch();
}
async function loadCatalog() {personalities=await api("/personalities");$("model-persona").replaceChildren(new Option("None",""),...Object.entries(personalities).map(([id,p])=>new Option(p.name,id)));
  const [p,m]=await Promise.all([api("/recipes"),api("/models")]); recipes=p;models=m;
  const prior=$("recipe-select").value, model=$("model-select").value;
  $("recipe-select").replaceChildren(...recipes.map(p=>new Option(`${p.name}${p.archived?" · archived":""} · ${p.id.slice(0,8)}`,p.id)));
  $("recipe-select").value=recipes.some(p=>p.id===prior)?prior:(recipes.find(p=>!p.archived&&p.recipe_id===new URLSearchParams(location.search).get("recipe"))||recipes.find(p=>p.default)||recipes[0]).id;
  $("model-select").replaceChildren(new Option("Choose a model…",""),...models.filter(m=>!m.error).map(m=>new Option(`${m.config.model} · ${m.config.snapshot} (${m.id})`,m.id)));
  $("model-select").value=models.some(m=>m.id===model)?model:"";
  const invalid=models.filter(m=>m.error); if(invalid.length) $("notice").textContent="Model files need attention: "+invalid.map(m=>m.id).join(", ");
  renderRecipe(); updateLaunch();
}
function renderRuns() {
  const visible=runs.filter(r=>r.cohort===$("recipe-select").value).sort((a,b)=>b.created_at.localeCompare(a.created_at));
  $("run-count").textContent=visible.length?`(${visible.length})`:"";$("runs-empty").hidden=!!visible.length;$("run-list").replaceChildren();
  for(const r of visible){const row=node("tr"),name=node("div",r.model);name.append(node("span",r.snapshot,"secondary"));cell(row,name);cell(row,badge(r.status));cell(row,`${r.completed_episodes}/${r.total_episodes} episodes · ${Math.round(r.completed_ticks/r.total_ticks*100)}%`);cell(row,fmt(r.report?.rating));cell(row,date(r.created_at));const actions=node("div",undefined,"controls");
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
  if(refreshing) {refreshPending=true;return;} refreshing=true;$("check-stability").disabled=true;const token=revision;
  try {
    const [all, ranking]=await Promise.all([api("/benchmarks"),api(`/leaderboard?cohort=${$("recipe-select").value}&track=${$("track").value}`)]);
    if(token!==revision)return;runs=all;renderRuns();updateLaunch();
    $("ranking").replaceChildren();$("ranking-empty").hidden=!!ranking.length;
    const signature=JSON.stringify(ranking.map(r=>[r.id,r.updated_at]));$("stability-result").replaceChildren();rankingSignature=signature;
    ranking.forEach(r=>{const row=node("tr"),name=node("div",r.model);name.append(node("span",r.snapshot,"secondary"));cell(row,r.dominance?.front??"Unresolved");cell(row,name);const profile=node("div");for(const [d,value]of Object.entries(r.report.rating_domains||{}))profile.append(node("div",`${d}: ${fmt(value.score)}`));cell(row,profile);cell(row,r.total_episodes);cell(row,date(r.updated_at));cell(row,button("Inspect run",()=>openRun(r.id)));$("ranking").append(row);});
    $("comparison-chart").hidden=true;
    if(selectedRun&&view==="details")await loadRun(token);
  } finally {refreshing=false;$("check-stability").disabled=false;if(refreshPending){refreshPending=false;await refresh();}}
}
async function openRun(id){revision++;selectedRun=id;episodeId="";episode=null;selectedAgent="";detailView="overview";showView("details");await loadRun(revision);}
async function loadRun(token=revision){
  const result=await api("/benchmarks/"+selectedRun);if(token!==revision)return;runDetail=result;
  $("detail-empty").hidden=true;$("run-detail").hidden=false;
  $("detail-title").textContent=result.model;$("detail-recipe").textContent=result.recipe_name;
  $("detail-meta").textContent=`${result.snapshot} · ${result.status} · parallel requests: ${result.parallelism??1} · active episodes: ${result.current_episodes?.length??(result.current_episode?1:0)} · ${result.id} · ${date(result.created_at)}`;
  $("run-progress").max=result.total_ticks;$("run-progress").value=result.completed_ticks;
  const scoreNotice=result.error||result.reference_error||(result.report&&result.report.rating==null?("Reference gain unavailable: one or more cases lack distinct reference anchors."):"");$("run-failure").hidden=!scoreNotice;$("run-failure").textContent=scoreNotice;
  $("pause").hidden=result.status!=="running";$("resume").hidden=!["paused","interrupted","failed"].includes(result.status);
  $("export").hidden=result.status!=="completed";$("export").href=`/api/benchmarks/${result.id}/export`;
  stats("run-stats",[["Reference gain",fmt(result.report?.rating)],["95% interval",result.report?.rating_ci95?result.report.rating_ci95.map(fmt).join(" – "):"Pending"],["Episodes completed",`${result.completed_episodes} / ${result.total_episodes}`],["Execution time",`${fmt((result.wall_seconds??0)/3600)} / ${fmt((result.max_wall_seconds??187200)/3600)} h`],["Progress",Math.round(result.completed_ticks/result.total_ticks*100)+"%"],["Recorded messages",fmt(result.telemetry?.messages)],["Generated tokens reported",fmt(result.telemetry?.reported_generated_tokens)]]);
  $("candidate-json").textContent=JSON.stringify(result.candidate,null,2);
  bars("domain-chart","Results by domain",Object.entries(result.report?.domains||{}).map(([k,v])=>[`${k} · ${domains[k]}`,v.score]));
  $("strata").replaceChildren();for(const [name,values]of Object.entries(result.report?.strata||{})){const card=node("div",undefined,"card");bars(card,"Raw attainment (%) by "+name,Object.entries(values));$("strata").append(card);}
  const previous=$("domain-filter").value;$("domain-filter").replaceChildren(new Option("All domains",""),...[...new Set(result.episodes.map(e=>e.domain))].map(d=>new Option(d+" · "+domains[d],d)));$("domain-filter").value=previous;
  renderEpisodes();
  const started=result.episodes.filter(e=>e.status!=="pending");$("episode-select").replaceChildren(...started.map(e=>new Option(`#${e.index} · ${e.domain} · seed ${e.seed} · ${e.role} · ${e.complexity}`,e.run_id)));
  if(!started.some(e=>e.run_id===episodeId))episodeId=started[0]?.run_id||"";$("episode-select").value=episodeId;
  await showDetail(detailView,false);
}
function renderEpisodes(){
  $("episodes").replaceChildren();for(const e of runDetail.episodes.filter(e=>!$("domain-filter").value||e.domain===$("domain-filter").value)){const row=node("tr");cell(row,`#${e.index}`);cell(row,`${e.domain} · ${e.complexity}`);cell(row,e.seed);cell(row,e.role);cell(row,`${e.completed_tick}/${e.ticks??240} · ${e.status}`);cell(row,fmt(e.score));const b=button("Explore",async()=>{episodeId=e.run_id;$("episode-select").value=episodeId;episode=null;await showDetail("agents");});b.disabled=e.status==="pending";cell(row,b);$("episodes").append(row);}
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
  $("episode-status").textContent=`${result.status} · ${result.completed_tick}/${result.manifest?.ticks??240} ticks`;
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
  $("model-backend").value=config.context_bytes_verified!==undefined&&!config.context_tokens?"compatible":"lmstudio";$("model-structured").value=config.structured_output||"compact_decision";$("model-constrain").checked=config.constrain_output??Boolean(config.model);
  $("model-managed").checked=config.managed_instance??!config.model;$("model-reasoning").checked=Boolean(config.reasoning_delimiters);$("model-timeout").value=config.request_timeout_seconds??(config.model?120:600);
  $("model-effort").value=config.reasoning_effort||"";
  $("model-context").value=config.context_tokens??config.context_bytes_verified??50000;updateBackend();$("model-track").value=config.budget_track||"opaque_compute";
  for(const [id,key]of [["temperature","temperature"],["top-p","top_p"],["top-k","top_k"],["presence-penalty","presence_penalty"],["frequency-penalty","frequency_penalty"],["repeat-penalty","repeat_penalty"],["seed","seed"],["input-price","input_price_per_million"],["output-price","output_price_per_million"],["max-output","max_output_tokens"],["max-input","max_input_tokens"],["key-env","api_key_env"],["key-file","api_key_file"]])$("model-"+id).value=config[key]??(key==="temperature"?0:"");
  $("model-persona").value=config.persona&&personalities[config.persona]?config.persona:"";$("model-personality").value=config.personality||"";$("model-prompt").value=config.system_prompt||"";
  $("model-key").value="";$("model-remove-key").checked=false;$("model-error").textContent="";
}
$("model-persona").addEventListener("change",()=>{const p=personalities[$("model-persona").value];if(p){$("model-personality").value=p.name;$("model-prompt").value=p.system_prompt;}});
function openModel(fresh=false){const m=models.find(m=>m.id===$("model-select").value);fillModel(fresh?"":m?.id||"",fresh?{}:m?.config||{});$("model-dialog").showModal();}
action("add-model",()=>openModel(true));action("edit-model",()=>openModel());action("close-model",()=>$("model-dialog").close());
action("model-select",()=>{const m=models.find(m=>m.id===$("model-select").value);if(m)$("track").value=m.config.budget_track;return refresh();},"change");
$("model-form").addEventListener("submit",async e=>{
  e.preventDefault();$("model-error").textContent="";$("save-model").disabled=true;
  try{const config={...modelOriginal},url=new URL($("model-address").value);if($("model-port").value)url.port=$("model-port").value;
    delete config.context_tokens;delete config.context_bytes_verified;config[$("model-backend").value==="lmstudio"?"context_tokens":"context_bytes_verified"]=Number($("model-context").value);
    Object.assign(config,{model:$("model-name").value.trim(),snapshot:$("model-snapshot").value.trim(),endpoint:url.href.replace(/\/$/,""),structured_output:$("model-structured").value,constrain_output:$("model-constrain").checked,budget_track:$("model-track").value});
    for(const [id,key]of [["temperature","temperature"],["top-p","top_p"],["top-k","top_k"],["presence-penalty","presence_penalty"],["frequency-penalty","frequency_penalty"],["repeat-penalty","repeat_penalty"],["seed","seed"],["input-price","input_price_per_million"],["output-price","output_price_per_million"],["max-output","max_output_tokens"],["max-input","max_input_tokens"]]){delete config[key];if($("model-"+id).value!=="")config[key]=Number($("model-"+id).value);}
    config.request_timeout_seconds=Number($("model-timeout").value);
    delete config.reasoning_effort;if($("model-backend").value==="lmstudio"&&$("model-effort").value)config.reasoning_effort=$("model-effort").value;
    delete config.managed_instance;if($("model-backend").value==="lmstudio")config.managed_instance=$("model-managed").checked;
    delete config.reasoning_delimiters;if(!config.constrain_output&&$("model-reasoning").checked)config.reasoning_delimiters=["<think>","</think>"];
    for(const [id,key]of [["key-env","api_key_env"],["key-file","api_key_file"]]){delete config[key];if($("model-"+id).value.trim())config[key]=$("model-"+id).value.trim();}
    delete config.persona;delete config.personality;delete config.system_prompt;const prompt=$("model-prompt").value.trim();if(prompt){config.system_prompt=prompt;if($("model-personality").value.trim())config.personality=$("model-personality").value.trim();const preset=personalities[$("model-persona").value];if(preset&&preset.system_prompt===prompt)config.persona=$("model-persona").value;}
    const body={config};if($("model-remove-key").checked)body.api_key="";else if($("model-key").value)body.api_key=$("model-key").value;
    const id=$("model-id").value;await api("/models/"+encodeURIComponent(id),body,"PUT");$("model-dialog").close();await loadCatalog();$("model-select").value=id;ModelHealth.dashboard();$("track").value=config.budget_track;$("notice").textContent="Model configuration saved.";updateLaunch();await refresh();
  }catch(error){$("model-error").textContent=error.message;}finally{$("save-model").disabled=false;}
});
action("import-model",async()=>{try{const file=$("import-model").files[0];if(!file)return;const value=JSON.parse(await file.text());if(!value.model||!value.endpoint)throw new Error("Choose a candidate model JSON, not a run or recipe file.");fillModel(file.name.replace(/\.json$/i,"").replace(/[^A-Za-z0-9_-]/g,"-"),value);}catch(error){$("model-error").textContent=error.message;}},"change");
action("start",async()=>{const recipe=recipes.find(p=>p.id===$("recipe-select").value);if(!recipe||recipe.archived)throw new Error("Select a current benchmark recipe first.");const r=await api("/benchmarks",{model_id:$("model-select").value,recipe_id:recipe.recipe_id,cohort:recipe.id,profile:$("candidate-profile").value||null,parallelism:Number($("parallelism").value)});await loadCatalog();$("recipe-select").value=r.cohort;renderRecipe();$("notice").textContent=`Started ${r.model} on ${r.recipe_name}.`;await openRun(r.id);await refresh();});
action("pause",async()=>{await api(`/benchmarks/${selectedRun}/pause`,{});await refresh();});
action("resume",async()=>{await api(`/benchmarks/${selectedRun}/resume`,{});await refresh();});
action("refresh",async()=>{await loadCatalog();await refresh();});
action("recipe-select",async()=>{revision++;selectedRun=null;runDetail=null;episode=null;episodeId="";$("detail-empty").hidden=false;$("run-detail").hidden=true;renderRecipe();renderRuns();$("ranking").replaceChildren();$("ranking-empty").hidden=true;$("comparison-chart").hidden=true;await refresh();},"change");
action("track",async()=>{$("stability-result").replaceChildren();revision++;$("ranking").replaceChildren();$("ranking-empty").hidden=true;$("comparison-chart").hidden=true;await refresh();},"change");action("domain-filter",renderEpisodes,"change");
action("episode-select",async()=>{revision++;episodeId=$("episode-select").value;episode=null;await showDetail(detailView);},"change");
action("message-agent",()=>loadMessages(false),"change");action("more-messages",()=>loadMessages(true));action("more-events",()=>loadEvents(true));
action("world-tick",async()=>{const tick=Number($("world-tick").value),state=await api(epPath()+`/state?tick=${tick}`);$("world-tick-value").textContent=tick;renderAgents(state);},"change");
action("load-decision",async()=>{if(!episodeId||!selectedAgent)return;$("decision").replaceChildren();const data=await api(epPath()+`/decision?agent=${encodeURIComponent(selectedAgent)}&tick=${$("decision-tick").value}`);for(const [label,value]of [["Saved observation",data.observation],["Model / policy response",data.response],["Inference calls",data.calls]]){const d=node("details");d.open=label==="Model / policy response";d.append(node("summary",label),node("pre",typeof value==="string"?value:JSON.stringify(value,null,2)));$("decision").append(d);}});
for(const b of document.querySelectorAll("[data-view]"))b.onclick=()=>{showView(b.dataset.view);if(selectedRun&&view==="details")loadRun().catch(reportError);};
for(const b of document.querySelectorAll("[data-detail]"))b.onclick=()=>showDetail(b.dataset.detail).catch(reportError);
(async()=>{await loadCatalog();await refresh();})().catch(reportError);
setInterval(()=>{if(!document.hidden&&!$("model-dialog").open&&runs.some(r=>r.status==="running"))refresh().catch(reportError);},4000);

function updateBackend(){const native=$("model-backend").value==="lmstudio";$("model-context-label").firstChild.textContent=native?"Maximum context (tokens) ":"Verified observation capacity (bytes) ";$("model-context").min=native?"1":"24000";$("model-effort").disabled=!native;}
action("model-backend",async()=>{if($("model-backend").value==="compatible")$("model-context").value=Math.max(24000,Number($("model-context").value)||24000);updateBackend();},"change");

$("check-stability").onclick=async()=>{
  const out=$("stability-result"),cohort=$("recipe-select").value,track=$("track").value,signature=rankingSignature;out.textContent="Computing matched seed bootstrap…";
  try {
    const s=await api(`/leaderboard/stability?cohort=${$("recipe-select").value}&track=${$("track").value}`);
    if(cohort!==$("recipe-select").value||track!==$("track").value||signature!==rankingSignature)return;
    if(!s.available){out.textContent=s.reason;return;}
    out.replaceChildren(node("p",`${s.seeds} seeds · ${s.bootstrap_resamples} joint bootstrap draws. ${s.primary_order.scope}`));
    const label=id=>s.labels?.[id]||id;
    const table=(headers,rows)=>{const wrap=node("div",undefined,"table-wrap"),t=node("table"),head=node("thead"),hr=node("tr"),body=node("tbody");for(const h of headers)hr.append(node("th",h));head.append(hr);for(const values of rows){const tr=node("tr");for(const value of values)cell(tr,value);body.append(tr);}t.append(head,body);wrap.append(t);return wrap;};
    out.append(table(["Candidate","Non-dominated front","Demonstrated superiors"],s.primary_order.ranking.map(r=>[label(r.system),r.front??"Insufficient seeds",r.dominated_by.map(label).join("; ")||"None demonstrated"])));
    out.append(table(["Left","Right","Non-compensatory relation"],s.primary_order.relations.map(r=>[label(r.left),label(r.right),r.relation.replaceAll("_"," ")])));
    const profileText={tradeoff:"Opposing domain advantages",left_strengths:"Left has demonstrated domain advantages",right_strengths:"Right has demonstrated domain advantages",unresolved:"No demonstrated domain difference",insufficient_seeds:"Insufficient seeds"};
    for(const p of s.comparisons){if(!p.domain_comparisons)continue;const detail=node("details");detail.open=p.verdict==="unresolved"&&p.profile_status==="tradeoff";detail.append(node("summary",`${label(p.left)} / ${label(p.right)}: ${profileText[p.profile_status]}`),table(["Domain","Left","Right","Difference","Domain-family 95% interval","Result"],p.domain_comparisons.map(d=>[domains[d.domain]||d.domain,fmt(d.left_mean),fmt(d.right_mean),fmt(d.delta),d.simultaneous_ci95?.map(fmt).join(" – ")||"unavailable",d.verdict.replaceAll("_"," ")])));if(p.aggregation){const a=p.aggregation,raw=p.raw_aggregation,audit=node("details");audit.append(node("summary","Diagnostic only: aggregation sensitivity"));audit.append(node("p",`Equal-domain mean: ${fmt(a.mean_difference)}; mean absolute domain difference: ${fmt(a.mean_absolute_domain_difference)}. Cancellation: ${fmt(100*a.cancelled_fraction)}%. ${a.minimum_weight_transfer_to_tie===null?"No nonnegative domain weighting can reach a tie for these observed means.":`Transferring ${fmt(100*a.minimum_weight_transfer_to_tie)} percentage points of total domain weight is enough to reach a tie.`}`));if(raw)audit.append(node("p",`Before reference calibration: difference ${fmt(raw.mean_difference)}, cancellation ${fmt(100*raw.cancelled_fraction)}%.`));audit.append(node("p",a.scope));detail.append(audit);}out.append(detail);}
    if(s.casewise_family){
      const names={left_majority_all_domains:"Left: strict-win majority in every domain",right_majority_all_domains:"Right: strict-win majority in every domain",unresolved:"Unresolved"};
      const casewise=node("details");casewise.append(node("summary","Casewise strict-win certificates"),node("p",s.casewise_family.scope),table(["Left","Right","All-domain certificate"],s.comparisons.map(p=>[label(p.left),label(p.right),(p.casewise_domains||[]).every(d=>d.verdict==="unavailable_design")?"Insufficient independent clusters":names[p.casewise_profile_status]||"Unresolved"])));
      for(const p of s.comparisons){const detail=node("details");detail.append(node("summary",`${label(p.left)} / ${label(p.right)}: per-domain seed clusters`),table(["Domain","Left wins","Right wins","Ties","Clusters","Adjusted p upper bound","Result"],(p.casewise_domains||[]).map(d=>[domains[d.domain]||d.domain,d.wins,d.losses,d.ties,d.clusters,fmt(d.family_adjusted_p_upper_bound),d.verdict.replaceAll("_"," ")])));casewise.append(detail);}out.append(casewise);
    }
    const raw=node("details");raw.append(node("summary","Raw metric: paired aggregate comparisons"),node("p","Bounded raw scores use the same cases and independent seed clusters. These intervals share the correction with the calibrated and strict-win comparison families; an aggregate advantage does not establish an advantage in every domain."),table(["Left","Right","Raw difference","Jointly adjusted 95% interval","Result"],s.comparisons.map(p=>[label(p.left),label(p.right),fmt(p.raw_metric?.delta),p.raw_metric?.simultaneous_ci95?.map(fmt).join(" – ")||"unavailable",p.raw_metric?.verdict?.replaceAll("_"," ")||"unavailable"])));out.append(raw);
    const diagnostic=node("details");diagnostic.append(node("summary","Diagnostic calibrated equal-domain averages"),table(["Left","Right","Mean difference","Jointly adjusted 95% interval"],s.comparisons.map(p=>[label(p.left),label(p.right),fmt(p.delta),p.simultaneous_ci95?.map(fmt).join(" – ")||"unavailable"])));out.append(diagnostic);
  }catch(e){out.textContent=e.message;}
};
