"""xCDN export for the static Explorer: writer, run files, index and the official parser."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from unimatrix.polis.explorer import (
    Annotated,
    ExplorerExport,
    Tagged,
    decimal,
    dumps,
    run_document,
)
from unimatrix.polis.recipe import PolisRecipes
from unimatrix.polis.report import build_report
from unimatrix.polis.simulate import run as play
from unimatrix.polis.world import World

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "explorer" / "vendor" / "xcdn.js"
NODE = shutil.which("node")


def parse_with_official_library(text):
    """Parse xCDN with the bundled official JavaScript library and summarise the result."""
    script = (
        "require(process.argv[1]); const fs = require('fs');"
        "const doc = xcdn.parse(fs.readFileSync(0, 'utf8'));"
        "const plain = v => JSON.parse(JSON.stringify(v, (k, x) => typeof x === 'bigint' ? Number(x) : x));"
        "console.log(JSON.stringify({prolog: doc.prolog.map(d => [d.name, d.value.unwrap()]),"
        "values: doc.values.map(v => ({tags: v.tags.map(t => t.name), value: plain(v.unwrap()),"
        "annotations: v.value.keys ? v.value.keys().filter(k => v.value.get(k).annotations.length)"
        ".map(k => [k, v.value.get(k).annotations.map(a => [a.name, a.args.map(x => plain(x.unwrap()))])]) : []}))}));"
    )
    done = subprocess.run([NODE, "-e", script, str(BUNDLE)], input=text, capture_output=True, text=True, encoding="utf8",
                          check=True)
    return json.loads(done.stdout)


def test_writer_produces_typed_xcdn():
    value = Tagged("run", {
        "id": "abc", "quoted key": 1, "true": False, "score": decimal(12.345678, 2), "nan": float("nan"),
        "text": 'He said "hi"\nbye', "when": datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc),
        "took": timedelta(minutes=14, seconds=30), "rows": Annotated("columns", ("a", "b"), [[1, 2.5], [3, None]]),
    })
    text = dumps(value)
    assert text.startswith("#run {")
    assert '"quoted key": 1' in text and '"true": false' in text
    assert 'score: d"12.35"' in text and "nan: null" in text
    assert 'when: t"2026-10-11T12:00:00Z"' in text and 'took: r"PT14M30S"' in text
    assert 'rows: @columns("a", "b") [[1, 2.5], [3, null]]' in text
    assert '"He said \\"hi\\"\\nbye"' in text


@pytest.fixture(scope="module")
def recipe():
    return PolisRecipes().get("standard-v2.1")


@pytest.mark.skipif(NODE is None, reason="node is needed to run the official xCDN parser")
@pytest.mark.parametrize("policy", ["naive", "passive"])
def test_run_files_round_trip_through_the_official_parser(recipe, policy):
    world = play(recipe, 1, policy)
    report = build_report(world)
    report["frames"] = world.frames
    run = dict(id="r" * 24, model="m", snapshot="s", status="completed", created_at="2026-10-11T10:00:00+00:00",
               updated_at="2026-10-11T10:14:30+00:00", recipe_hash="h" * 64,
               usage=dict(calls=52, input_tokens=100, generated_tokens=10, seconds=870.0),
               score=dict(total=report["score"]["total"], collapsed=0, relative_to_expert=0.7, wellby=90.0))
    decisions = {w: dict(week=w, raw='{"actions": []}', parse_error=None,
                         usage=dict(reasoning="Check the well first.", input_tokens=1300, generated_tokens=80,
                                    latency=2.25, finish_reason="stop"))
                 for w in range(1, 53)}
    decisions[1]["observation"] = World(recipe, 1).observe()
    text = run_document(run, recipe, {"1": report}, {"expert": 8000.0, "passive": 0.0}, {"1": decisions})
    parsed = parse_with_official_library(text)
    assert dict(parsed["prolog"])["format"] == "unimatrix.explorer.run/1"
    head, weeks = parsed["values"][0], parsed["values"][1:]
    assert head["tags"] == ["run"] and len(weeks) == len(world.frames)
    assert all(w["tags"] == ["week"] for w in weeks)
    assert head["value"]["score"]["total"] == f"{report['score']['total']:.3f}"
    assert head["value"]["score"]["rule"] == report["score"]["rule"]
    assert head["value"]["score"]["earned"] == f"{report['score']['earned']:.3f}"
    assert head["value"]["score"]["penalty"] == f"{report['score']['penalty']:.3f}"
    assert head["value"]["seeds"][0]["penalty"] == f"{report['score']['penalty']:.3f}"
    assert head["value"]["duration"] and head["value"]["recipe"]["weeks"] == 52
    assert [s["seed"] for s in head["value"]["seeds"]] == [1]
    first = weeks[3]["value"]
    columns = dict(weeks[3]["annotations"])["residents"][0][1]
    assert columns[:3] == ["id", "status", "activity"]
    assert all(len(row) == len(columns) for row in first["residents"])
    assert first["week"] == 3 and set(first["points"]) == {f"D{i}" for i in range(1, 9)}
    assert bool(first["actions"]) == (policy != "passive")
    assert first["decision"] == {"answer": '{"actions": []}', "reasoning": "Check the well first.",
                                 "input_tokens": 1300, "generated_tokens": 80, "finish_reason": "stop",
                                 "latency": "2.250"}
    assert "WEEK 1 of 52" in weeks[1]["value"]["decision"]["briefing"] and "decision" not in weeks[0]["value"]


def test_exports_write_runs_and_a_combined_index(tmp_path, recipe):
    files = []
    for ident, policy in (("a" * 24, "expert"), ("b" * 24, "passive")):
        world = play(recipe, 1, policy)
        report = dict(build_report(world), frames=world.frames)
        record = dict(id=ident, kind="model", model="test-model", status="completed", recipe_hash="h" * 64)
        files.append(ExplorerExport(tmp_path).write_run(record, recipe, {"1": report}, {}))
    assert len(files) == 2 and all(f.is_file() and f.suffix == ".xcdn" for f in files)
    index = (tmp_path / "data" / "index.xcdn").read_text(encoding="utf8")
    assert index.count("#entry {") == 2 and "runs: 2" in index
    assert "badge" not in index and "decision: #decision" not in files[0].read_text(encoding="utf8")
    assert (tmp_path / "vendor" / "village.js").read_bytes() == (
        ROOT / "src" / "unimatrix" / "web" / "static" / "society-village.js").read_bytes()
    ExplorerExport(tmp_path).remove_run(files[0].stem)
    assert not files[0].exists()
    assert (tmp_path / "data" / "index.xcdn").read_text(encoding="utf8").count("#entry {") == 1


@pytest.mark.skipif(NODE is None, reason="node is needed to run the Explorer leaderboard")
def test_leaderboard_ranks_by_final_score_without_deducting_twice():
    script = """
        global.window = {ExplorerData: {store: {}}, ExplorerUI: {}};
        global.document = {readyState: 'loading', addEventListener() {}};
        require(process.argv[1]);
        const entry = (id, total, collapsed, kind = "model") => ({
            id, model: id, snapshot: '', revision: 'rev', total, kind,
            fullyCollapsed: collapsed, created: '2026-10-12'
        });
        const recipe = {entries: [entry('flash', 12254.443, false),
            entry('27b', 11395.480, true), entry('expert', 8384.956, false, 'reference')]};
        const rows = window.ExplorerApp.boardRows(recipe, 'rev', false);
        console.log(JSON.stringify(rows.map(r => [r.entry.id, r.rank, r.entry.total])));
    """
    done = subprocess.run([NODE, "-e", script, str(ROOT / "explorer" / "explorer.js")],
                          capture_output=True, text=True, encoding="utf8", check=True)
    assert json.loads(done.stdout) == [["flash", 1, 12254.443], ["27b", 2, 11395.480]]


def test_explorer_rejects_scripted_runs_before_writing(tmp_path, recipe):
    report = build_report(play(recipe, 1, "expert"))
    record = dict(id="c" * 24, kind="reference", model="expert steward")
    with pytest.raises(ValueError, match="model runs only"):
        ExplorerExport(tmp_path).write_run(record, recipe, {"1": report}, {})
    assert not list(tmp_path.rglob("*.xcdn"))


@pytest.mark.skipif(NODE is None, reason="node is needed to run the Explorer parser")
def test_explorer_imports_only_model_runs():
    script = r'''
        require(process.argv[1]);
        global.window = {xcdn: global.xcdn};
        require(process.argv[2]);
        const D = window.ExplorerData;
        const parse = (format, body) => D.parseText('$format: "unimatrix.explorer.' + format + '/1"\n' + body, 'test.xcdn');
        const result = D.readIndex(parse('index', '#entry {run: "ref", kind: "reference"}\n#entry {run: "model", kind: "model"}'));
        const rejected = [];
        for (const [kind, body, read] of [
            ['entry', '#entry {run: "ref", kind: "reference"}', D.readEntryFile],
            ['run', '#run {id: "ref", kind: "reference"}\n#week {week: 0}', D.readRun]]) {
            try { read(parse(kind, body)); rejected.push(false); }
            catch (err) { rejected.push(err.message.includes('model runs only')); }
        }
        console.log(JSON.stringify({entries: result.entries.map(e => e.id), warnings: result.warnings.length, rejected}));
    '''
    done = subprocess.run([NODE, "-e", script, str(BUNDLE), str(ROOT / "explorer" / "data.js")],
                          capture_output=True, text=True, encoding="utf8", check=True)
    assert json.loads(done.stdout) == dict(entries=["model"], warnings=1, rejected=[True, True])
