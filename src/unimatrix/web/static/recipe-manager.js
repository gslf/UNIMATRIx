"use strict";
const $ = id => document.getElementById(id);
const node = (tag, text, cls) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
};
let drafts = [], recipes = [], runs = [], selected = null, selectionRevision = 0, deletion = null;
let busy = false;
async function api(path, body, method = "POST") {
  const response = await fetch("/api" + path, body === undefined ? {cache:"no-store"} :
    {method, headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function error(e) { $("error").textContent = e.message; $("error").hidden = false; }
function facts(target, entries) {
  target.replaceChildren(...entries.map(([label, value]) => {
    const box = node("div"); box.append(node("dt",label),node("dd",String(value))); return box;
  }));
}
function benchmarkLink(id) {
  const link = node("a","Open in Benchmarks","button");
  link.href = "/?recipe=" + encodeURIComponent(id);
  return link;
}
function lock(value) {
  busy = value;
  for (const id of ["refresh","draft","split","create","delete-draft","confirm-delete","cancel-delete"])
    $(id).disabled = value;
  for (const button of $("recipes").querySelectorAll("button[data-delete]")) button.disabled = value;
  if (!value) renderDraft();
}
function renderDraft() {
  const spec = selected && ($("split").value === "holdout" ? selected.holdout : selected.recipe);
  $("draft-summary").hidden = !spec;
  $("create").disabled = busy || !spec;
  $("delete-draft").disabled = busy || !selected;
  $("split").disabled = busy || !selected;
  if (!spec) return;
  $("draft-name").textContent = spec.name;
  $("draft-description").textContent = spec.description;
  facts($("draft-facts"), [
    ["Recipe ID", spec.id], ["Episodes", spec.cases.length],
    ["Domains", [...new Set(spec.cases.map(c => c.domain))].join(", ")],
    ["Agents per episode", spec.peers.length + 1],
  ]);
}
async function selectDraft() {
  const revision = ++selectionRevision, id = $("draft").value;
  selected = null; renderDraft();
  if (!id) return;
  const record = await api("/recipe-lab/drafts/" + encodeURIComponent(id));
  if (revision !== selectionRevision) return;
  selected = record;
  $("split").options[1].disabled = !record.holdout;
  if (!record.holdout) $("split").value = "development";
  renderDraft();
}
function renderCatalog() {
  const current = recipes.filter(r => !r.archived);
  $("catalog-stats").replaceChildren(...[
    ["Available recipes",current.length],["Saved drafts",drafts.length],
    ["Benchmark runs",runs.length],["Completed runs",runs.filter(r=>r.status==="completed").length],
  ].map(([label,value])=>{const box=node("div",undefined,"stat");box.append(node("strong",String(value)),node("span",label));return box;}));
  $("recipes").replaceChildren();
  for (const recipe of current) {
    const card = node("article",undefined,"card"), matching = runs.filter(r=>r.cohort===recipe.id);
    card.append(node("h3",recipe.name),node("p",recipe.description,"muted small"));
    const list = node("dl",undefined,"manager-facts");
    facts(list, [["Recipe ID",recipe.recipe_id],["Episodes per run",recipe.cases],
      ["Agents per episode",(recipe.spec?.peers?.length ?? 7)+1],
      ["Completed runs",matching.filter(r=>r.status==="completed").length],
      ["Other runs",matching.filter(r=>r.status!=="completed").length],
      ["Revision",recipe.recipe_hash.slice(0,10)]]);
    card.append(list);
    const controls = node("div",undefined,"controls");
    controls.append(benchmarkLink(recipe.recipe_id));
    const remove = node("button","Delete recipe","danger");
    remove.type = "button"; remove.disabled = !recipe.deletable;
    if (recipe.deletable) {
      remove.dataset.delete = recipe.recipe_id;
      remove.onclick = () => prepareDelete("/recipe-lab/recipes/"+encodeURIComponent(recipe.recipe_id)).catch(error);
    }
    controls.append(remove); card.append(controls);
    if (!recipe.deletable) card.append(node("p",recipe.deletion_note,"muted small"));
    $("recipes").append(card);
  }
  if (!current.length) $("recipes").append(node("p","No published recipes.","muted"));
}
async function refresh() {
  const prior = $("draft").value || new URLSearchParams(location.search).get("draft");
  const data = await Promise.all([api("/recipe-lab/drafts"),api("/recipes"),api("/benchmarks")]);
  [drafts, recipes, runs] = data;
  $("draft").replaceChildren(new Option("Choose a draft…",""),...drafts.map(d=>
    new Option(d.name+" · "+d.episodes+" episodes · "+d.id.slice(-6),d.id)));
  $("draft").value = drafts.some(d=>d.id===prior) ? prior : "";
  $("draft-empty").hidden = drafts.length > 0;
  renderCatalog(); await selectDraft();
}
async function prepareDelete(path) {
  if (busy) return;
  lock(true); $("error").hidden = true;
  try {
    const impact = await api(path+"/deletion");
    deletion = path;
    $("delete-name").textContent = impact.name;
    facts($("delete-impact"), [
      ["Recipe IDs",impact.recipe_ids.join(", ")],["Saved drafts",impact.drafts],
      ["Lab evaluations",impact.evaluations],["Evaluation previews",impact.previews],
      ["Benchmark runs",impact.benchmarks],
    ]);
    $("delete-error").hidden = true; $("delete-dialog").showModal();
  } finally { lock(false); }
}
$("draft").onchange = () => selectDraft().catch(error);
$("split").onchange = renderDraft;
$("refresh").onclick = async () => {
  lock(true); $("error").hidden = true;
  try { await refresh(); } catch(e) { error(e); } finally { lock(false); }
};
$("create").onclick = async () => {
  if (!selected || busy) return;
  const id = selected.id, split = $("split").value;
  lock(true); $("error").hidden = true; $("notice").replaceChildren();
  try {
    const result = await api("/recipe-lab/drafts/"+encodeURIComponent(id)+"/publish",{split});
    $("notice").append(document.createTextNode(result.name + (result.already_saved ?
      " is already available in Benchmarks. " : " was created. ")),benchmarkLink(result.id));
    await refresh();
  } catch(e) { error(e); } finally { lock(false); }
};
$("delete-draft").onclick = () => {
  if (selected) prepareDelete("/recipe-lab/drafts/"+encodeURIComponent(selected.id)).catch(error);
};
$("cancel-delete").onclick = () => $("delete-dialog").close();
$("delete-dialog").addEventListener("cancel",event=>{if(busy)event.preventDefault();});
$("confirm-delete").onclick = async () => {
  if (!deletion || busy) return;
  lock(true); $("delete-error").hidden = true;
  try {
    const result = await api(deletion,{},"DELETE");
    deletion = null; $("delete-dialog").close();
    $("notice").textContent = result.name + " and its associated data were deleted.";
    await refresh();
  } catch(e) {
    if ($("delete-dialog").open) { $("delete-error").textContent=e.message;$("delete-error").hidden=false; }
    else error(e);
  } finally { lock(false); }
};
(async()=>{lock(true);try{await refresh();}finally{lock(false);}})().catch(error);
