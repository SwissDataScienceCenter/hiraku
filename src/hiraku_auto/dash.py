"""Rendering a project as one self-contained HTML page.

Read-only. `tree.to_dict` is the data contract; this module turns it into HTML and nothing else, so
the same snapshot feeds the terminal, the agent, the Streamlit app and this page.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from .config import Project
from .tree import Forest, build, to_dict


def section(sections: dict[str, str], *names: str) -> str:
    for name in names:
        for key, value in sections.items():
            if key.lower().startswith(name.lower()):
                return value
    return ""


# --------------------------------------------------------------------------- markdown


_INLINE = (
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
)


def inline(text: str) -> str:
    out = html.escape(text)
    for pattern, replacement in _INLINE:
        out = pattern.sub(replacement, out)
    return out


def markdown(text: str) -> str:
    """Minimal renderer: headings, paragraphs, blockquotes, fenced code, lists, tables."""
    if not text.strip():
        return '<p class="muted">Not recorded.</p>'
    out: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            block: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote>{inline(' '.join(quote))}</blockquote>")
            continue

        if stripped.startswith("|"):
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [c.strip() for c in lines[index].strip().strip("|").split("|")]
                rows.append(cells)
                index += 1
            if len(rows) >= 2 and set(rows[1][0]) <= set("-: "):
                head, body_rows = rows[0], rows[2:]
            else:
                head, body_rows = [], rows
            table = ["<div class='scroll'><table>"]
            if head:
                table.append(
                    "<thead><tr>"
                    + "".join(f"<th>{inline(c)}</th>" for c in head)
                    + "</tr></thead>"
                )
            table.append("<tbody>")
            for row in body_rows:
                table.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
            table.append("</tbody></table></div>")
            out.append("".join(table))
            continue

        if re.match(r"^([-*]|\d+\.)\s+", stripped):
            ordered = bool(re.match(r"^\d+\.\s+", stripped))
            items: list[str] = []
            while index < len(lines) and re.match(r"^([-*]|\d+\.)\s+", lines[index].strip()):
                items.append(re.sub(r"^([-*]|\d+\.)\s+", "", lines[index].strip()))
                index += 1
                while (
                    index < len(lines)
                    and lines[index].strip()
                    and not re.match(r"^([-*]|\d+\.)\s+", lines[index].strip())
                    and lines[index].startswith((" ", "\t"))
                ):
                    items[-1] += " " + lines[index].strip()
                    index += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{tag}>")
            continue

        paragraph: list[str] = []
        while index < len(lines) and lines[index].strip() and not re.match(
            r"^(#{1,6}\s|\||>|```|[-*]\s|\d+\.\s)", lines[index].strip()
        ):
            paragraph.append(lines[index].strip())
            index += 1
        if paragraph:
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
        else:
            index += 1
    return "".join(out)


CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --panel:#fff; --ink:#1a1a19; --muted:#6b6b66; --line:#e3e2de;
  --accent:#3b5bdb; --supports:#2f7a4f; --refutes:#a13d2f; --inconclusive:#96701f;
  --running:#2b6f8f; --proposed:#7a6ba8; --chip:#f2f1ee;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Inter,sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#17171a; --panel:#1e1e22; --ink:#e8e8e4; --muted:#9a9a93; --line:#31313a;
  --accent:#8da2fb; --supports:#63b184; --refutes:#e08f80; --inconclusive:#d8b45e;
  --running:#71b6d4; --proposed:#b3a3dd; --chip:#282830;
}}
:root[data-theme="dark"]{
  --bg:#17171a; --panel:#1e1e22; --ink:#e8e8e4; --muted:#9a9a93; --line:#31313a;
  --accent:#8da2fb; --supports:#63b184; --refutes:#e08f80; --inconclusive:#d8b45e;
  --running:#71b6d4; --proposed:#b3a3dd; --chip:#282830;
}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans);
  -webkit-font-smoothing:antialiased;overflow-x:hidden}
a{color:var(--accent)}
code{font-family:var(--mono);font-size:.87em;background:var(--chip);
  padding:.1em .35em;border-radius:4px}
pre{background:var(--chip);padding:.8rem 1rem;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{margin:.8rem 0;padding:.6rem .9rem;border-left:3px solid var(--line);
  color:var(--muted);background:var(--chip);border-radius:0 6px 6px 0}
.wrap{max-width:1500px;margin:0 auto;padding:1.5rem 1.5rem 4rem}
header.top{display:flex;flex-wrap:wrap;gap:1rem;align-items:baseline;
  justify-content:space-between;padding-bottom:1rem;border-bottom:1px solid var(--line);margin-bottom:1.5rem}
header.top h1{font-size:1.35rem;margin:0;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:.85rem;margin-top:.25rem}
.stats{display:flex;gap:1.25rem;flex-wrap:wrap;font-size:.82rem;color:var(--muted)}
.stats b{color:var(--ink);font-variant-numeric:tabular-nums}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1.5rem;align-items:start}
@media (max-width:1080px){.layout{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:1.1rem 1.25rem;margin-bottom:1.25rem}
.card>h2{margin:0 0 .2rem;font-size:1.02rem;letter-spacing:-.01em}
.goalmeta{font-size:.8rem;color:var(--muted);margin-bottom:.9rem;font-variant-numeric:tabular-nums}
.tree,.tree ul{list-style:none;margin:0;padding:0}
.tree ul{margin-left:.85rem;padding-left:.85rem;border-left:1px solid var(--line)}
.tree li{margin:.2rem 0}
.node{display:flex;flex-wrap:wrap;gap:.45rem;align-items:center;width:100%;
  background:none;border:1px solid transparent;border-radius:8px;padding:.4rem .55rem;
  cursor:pointer;text-align:left;color:inherit;font:inherit}
.node:hover{background:var(--chip)}
.node[aria-current="true"]{border-color:var(--accent);background:var(--chip)}
.node:focus-visible,.toggle:focus-visible,summary:focus-visible{outline:2px solid var(--accent);
  outline-offset:2px}
@media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
.node .name{font-family:var(--mono);font-size:.83rem}
.dot{width:8px;height:8px;border-radius:50%;flex:none;background:var(--muted)}
.dot.supports{background:var(--supports)} .dot.refutes{background:var(--refutes)}
.dot.inconclusive{background:var(--inconclusive)} .dot.running{background:var(--running)}
.dot.proposed,.dot.approved{background:var(--proposed)}
.chip{font-size:.68rem;letter-spacing:.03em;text-transform:uppercase;color:var(--muted);
  background:var(--chip);border-radius:999px;padding:.1rem .45rem;white-space:nowrap}
.chip.v-supports{color:var(--supports)} .chip.v-refutes{color:var(--refutes)}
.chip.v-inconclusive{color:var(--inconclusive)}
.chip.agent{color:var(--accent)}
.detail{position:sticky;top:1.5rem}
.detail h2{margin:0;font-size:1.05rem;font-family:var(--mono)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:.2rem .9rem;font-size:.83rem;margin:.8rem 0 0}
.kv dt{color:var(--muted)} .kv dd{margin:0;font-variant-numeric:tabular-nums}
.sec{margin-top:1.2rem;padding-top:1rem;border-top:1px solid var(--line)}
.sec>h3{margin:0 0 .4rem;font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.sec p:first-child{margin-top:0} .sec p:last-child{margin-bottom:0}
.scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:.8rem}
th,td{text-align:left;padding:.35rem .55rem;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.05em}
td{font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.warn{border-color:var(--inconclusive)}
.warn h2{color:var(--inconclusive)}
.warn ul{margin:.5rem 0 0;padding-left:1.1rem;font-size:.85rem}
.empty{color:var(--muted);font-size:.88rem}
.badge{display:inline-block;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;
  padding:.12rem .4rem;border-radius:4px;background:var(--chip);color:var(--muted)}
.badge.no{color:var(--refutes)}
.toggle{background:none;border:1px solid var(--line);border-radius:8px;color:var(--muted);
  padding:.3rem .6rem;font:inherit;font-size:.78rem;cursor:pointer}
.toggle:hover{color:var(--ink)}
details{margin-top:.6rem} summary{cursor:pointer;font-size:.85rem;color:var(--muted)}
"""

JS = """
const M = window.__HIRAKU__;
const pane = document.getElementById('detail');

function esc(s){const d=document.createElement('div');d.textContent=s==null?'':String(s);return d.innerHTML}
function chip(text, cls){return text?`<span class="chip ${cls||''}">${esc(text)}</span>`:''}

function runTable(runs){
  if(!runs.length) return '<p class="muted">No runs recorded.</p>';
  const skip=new Set(['experiment_id']);
  const cols=Object.keys(runs[0]).filter(c=>!skip.has(c)&&runs.some(r=>r[c]!==''));
  return `<div class="scroll"><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${runs.map(r=>`<tr>${cols.map(c=>{
      let v=r[c];
      if(c==='reproducible') return `<td><span class="badge ${v==='true'?'':'no'}">${esc(v)}</span></td>`;
      if(c==='commit') return `<td><code>${esc(String(v).slice(0,10))}</code></td>`;
      return `<td>${esc(v)}</td>`;
    }).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function select(id, scroll){
  const e = M.experiments[id];
  if(!e) return;
  document.querySelectorAll('.node').forEach(n=>n.setAttribute('aria-current', n.dataset.id===id?'true':'false'));
  const f = e.front||{};
  const rows = [
    ['Goal', f.goal||e.goal||'—'], ['Parent', e.parent||(e.parents||[]).join(', ')||'— (root)'],
    ['Kind', f.kind||'—'], ['Status', f.status||'—'], ['Verdict', f.verdict||'—'],
    ['Origin', f.origin||'—'], ['Branch', f.branch||'—'], ['Mode', f.mode||'—'],
    ['Created', f.created||'—'], ['Concluded', f.concluded||'—'],
    ['Children', (e.children||[]).length?e.children.join(', '):'none'],
    ['Runs', String((e.runs||[]).length)], ['Record', e.source],
  ];
  pane.innerHTML = `
    <div class="card detail">
      <h2>${esc(id)}</h2>
      <div style="margin-top:.4rem">${chip(f.kind)}${chip(f.status)}
        ${chip(f.verdict,'v-'+(f.verdict||''))}${chip(f.origin, String(f.origin||'').startsWith('agent')?'agent':'')}</div>
      ${f.expectation?`<div class="sec"><h3>Expectation</h3><p>${esc(f.expectation)}</p></div>`:
        '<div class="sec"><h3>Expectation</h3><p class="muted">Not recorded — this node cannot be triaged.</p></div>'}
      <dl class="kv">${rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>
      <div class="sec"><h3>Runs</h3>${runTable(e.runs||[])}</div>
      ${(e.rendered||[]).map(([h,b])=>`<div class="sec"><h3>${esc(h)}</h3>${b}</div>`).join('')}
    </div>`;
  if(scroll) pane.scrollIntoView({block:'nearest', behavior:'smooth'});
}

document.addEventListener('click', ev=>{
  const btn = ev.target.closest('.node');
  if(btn) select(btn.dataset.id, true);
});
document.getElementById('theme').addEventListener('click', ()=>{
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur==='dark' ? 'light' : cur==='light' ? '' : 'dark';
  if(next) document.documentElement.setAttribute('data-theme', next);
  else document.documentElement.removeAttribute('data-theme');
});
const first = document.querySelector('.node');
if(first) select(first.dataset.id, false);
"""


def roots_of(model: dict[str, Any], goal_id: str) -> list[str]:
    experiments = model["experiments"]
    return sorted(
        exp_id
        for exp_id, exp in experiments.items()
        if exp["goal"] == goal_id
        and not (exp["parent"] in experiments or any(p in experiments for p in exp["parents"]))
    )


def node_html(model: dict[str, Any], exp_id: str, seen: set[str]) -> str:
    if exp_id in seen:
        return f'<li class="muted">{html.escape(exp_id)} (cycle)</li>'
    seen.add(exp_id)
    exp = model["experiments"][exp_id]
    front = exp["front"]
    verdict = str(front.get("verdict") or "")
    status = str(front.get("status") or "")
    origin = str(front.get("origin") or "")
    dot = verdict or status or ""
    chips = "".join(
        [
            f'<span class="chip">{html.escape(str(front.get("kind")))}</span>'
            if front.get("kind")
            else "",
            f'<span class="chip">{html.escape(status)}</span>' if status else "",
            f'<span class="chip v-{html.escape(verdict)}">{html.escape(verdict)}</span>'
            if verdict
            else "",
            f'<span class="chip agent">{html.escape(origin)}</span>'
            if origin.startswith("agent")
            else "",
            f'<span class="chip">{len(exp["runs"])} run{"s" if len(exp["runs"]) != 1 else ""}</span>'
            if exp["runs"]
            else "",
        ]
    )
    children = "".join(node_html(model, child, seen) for child in exp["children"])
    kids = f"<ul>{children}</ul>" if children else ""
    return (
        f'<li><button class="node" data-id="{html.escape(exp_id)}">'
        f'<span class="dot {html.escape(dot)}"></span>'
        f'<span class="name">{html.escape(exp_id)}</span>{chips}</button>{kids}</li>'
    )


def render(model: dict[str, Any]) -> str:
    experiments = model["experiments"]
    goals = model["goals"]
    project = model["project"]

    concluded = sum(1 for e in experiments.values() if e["front"].get("status") == "concluded")
    open_nodes = sum(
        1 for e in experiments.values() if e["front"].get("status") in {"running", "approved"}
    )
    pending = sum(1 for e in experiments.values() if e["front"].get("status") == "proposed")
    agent_nodes = sum(
        1 for e in experiments.values() if str(e["front"].get("origin") or "").startswith("agent")
    )

    # Goals follow research/config.md's active_goals, so the page's reading order matches the
    # project's own priority rather than the filesystem's.
    declared = project["front"].get("active_goals")
    order = [g for g in declared if g in goals] if isinstance(declared, list) else []
    order += [g for g in goals if g not in order]

    goal_cards: list[str] = []
    placed: set[str] = set()
    for goal_id in order:
        goal = goals[goal_id]
        front = goal["front"]
        roots = roots_of(model, goal_id)
        seen: set[str] = set()
        tree = "".join(node_html(model, root, seen) for root in roots)
        placed |= seen
        arrow = "↓" if front.get("direction") == "minimize" else "↑"
        target = front.get("target")
        meta = " · ".join(
            part
            for part in [
                f'<code>{html.escape(str(front.get("primary_metric") or "—"))}</code> {arrow}',
                f"target {target}" if target is not None else "",
                f'status {html.escape(str(front.get("status") or "—"))}',
                f"{len(seen)} node{'s' if len(seen) != 1 else ''}",
            ]
            if part
        )
        question = section(goal["sections"], "Question")
        body = tree or '<li class="empty">No experiments yet.</li>'
        goal_cards.append(
            f'<section class="card"><h2>{html.escape(goal_id)}</h2>'
            f'<div class="goalmeta">{meta}</div>'
            f"{markdown(question) if question else ''}"
            f'<ul class="tree">{body}</ul>'
            f"</section>"
        )

    orphans = sorted(set(experiments) - placed)
    if orphans:
        rows = "".join(node_html(model, o, set()) for o in orphans)
        goal_cards.append(
            '<section class="card warn"><h2>Unattached experiments</h2>'
            '<p class="empty">These records name no reachable goal, so they appear under no tree.</p>'
            f'<ul class="tree">{rows}</ul></section>'
        )

    proposal_rows = "".join(
        f'<tr><td><code>{html.escape(p["id"])}</code></td>'
        f'<td>{html.escape(str(p["front"].get("goal") or "—"))}</td>'
        f'<td>{html.escape(str(p["front"].get("origin") or "—"))}</td>'
        f'<td>{html.escape(str(p["front"].get("status") or "—"))}</td>'
        f'<td>{html.escape(str(p["front"].get("proposed_on") or "—"))}</td></tr>'
        for p in model["proposals"]
    )
    proposals_card = (
        '<section class="card"><h2>Proposals</h2>'
        '<div class="scroll"><table><thead><tr><th>ID</th><th>Goal</th><th>Origin</th>'
        "<th>Status</th><th>Proposed</th></tr></thead>"
        f"<tbody>{proposal_rows}</tbody></table></div>"
        + "".join(
            f'<details><summary>{html.escape(p["id"])} — hypothesis and decision</summary>'
            f'{markdown(section(p["sections"], "Hypothesis"))}'
            f'<h3 style="font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)">Decision</h3>'
            f'{markdown(section(p["sections"], "Decision"))}</details>'
            for p in model["proposals"]
        )
        + "</section>"
        if model["proposals"]
        else ""
    )

    warnings_card = (
        '<section class="card warn"><h2>Record checks</h2><ul>'
        + "".join(f"<li>{inline(w)}</li>" for w in model["warnings"])
        + "</ul></section>"
        if model["warnings"]
        else ""
    )

    # Drop the file's own H1 so it does not double the card's heading.
    board = re.sub(r"^#\s+[^\n]*\n", "", model["leaderboard"].lstrip(), count=1)
    leaderboard_card = (
        f'<section class="card"><h2>Leaderboard</h2>{markdown(board)}</section>'
        if board.strip()
        else ""
    )

    payload = {
        "experiments": {
            exp_id: {
                "front": exp["front"],
                "goal": exp["goal"],
                "parent": exp["parent"],
                "parents": exp["parents"],
                "children": exp["children"],
                "runs": exp["runs"],
                "source": exp["source"],
                "rendered": [
                    [name, markdown(body)]
                    for name, body in exp["sections"].items()
                    if body.strip() and not name.lower().startswith("children")
                ],
            }
            for exp_id, exp in experiments.items()
        }
    }

    # default=str because PyYAML turns bare dates into date objects; escape "</" so a
    # record containing "</script>" cannot break out of the embedded payload.
    payload_json = json.dumps(payload, default=str).replace("</", "<\\/")

    bounds = project["front"].get("bounds") or {}
    bounds_text = (
        " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in bounds.items())
        if isinstance(bounds, dict)
        else ""
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(project["name"]))} — experiment tree</title>
<style>{CSS}</style></head><body><div class="wrap">
<header class="top">
  <div>
    <h1>{html.escape(str(project["name"]))}</h1>
    <div class="sub">Generated from the markdown records — read-only. The records are the source of truth.</div>
  </div>
  <div style="display:flex;gap:1.25rem;align-items:center;flex-wrap:wrap">
    <div class="stats">
      <span><b>{len(goals)}</b> goals</span>
      <span><b>{len(experiments)}</b> experiments</span>
      <span><b>{concluded}</b> concluded</span>
      <span><b>{open_nodes}</b> open</span>
      <span><b>{pending}</b> proposed</span>
      <span><b>{agent_nodes}</b> agent-originated</span>
      <span><b>{len(model["runs"])}</b> runs</span>
    </div>
    <button class="toggle" id="theme">theme</button>
  </div>
</header>
{f'<p class="sub" style="margin-top:-.75rem;margin-bottom:1.25rem">{html.escape(bounds_text)}</p>' if bounds_text else ''}
{warnings_card}
<div class="layout">
  <div>{''.join(goal_cards)}{proposals_card}{leaderboard_card}</div>
  <div id="detail"></div>
</div>
</div>
<script>window.__HIRAKU__={payload_json};</script>
<script>{JS}</script>
</body></html>"""




def render_project(project: Project, forest: Forest | None = None) -> str:
    """Convenience: build the snapshot and render it."""
    forest = forest or build(project)
    model = to_dict(forest)
    return render(adapt(model, project))


def adapt(model: dict[str, Any], project: Project) -> dict[str, Any]:
    """Shape `tree.to_dict` output into what `render` expects."""
    from .records import read_record

    project_front: dict[str, Any] = {}
    project_sections: dict[str, str] = {}
    if project.config_md.is_file():
        record = read_record(project.config_md)
        project_front, project_sections = record.front, record.sections

    experiments = {
        nid: {
            "id": nid,
            "front": data["front"],
            "sections": data["sections"],
            "goal": data["goal"],
            "parent": data["parent"],
            "parents": data["parents"],
            "children": data["children"],
            "runs": data["runs"],
            "source": data["path"],
            "dir_name": nid,
        }
        for nid, data in model["experiments"].items()
    }
    goals = {
        gid: {"id": gid, "front": data["front"], "sections": data["sections"], "source": ""}
        for gid, data in model["goals"].items()
    }
    proposals = [
        {"id": pid, "front": data["front"], "sections": data["sections"], "source": ""}
        for pid, data in model["proposals"].items()
    ]
    leaderboard = ""
    if project.leaderboard.is_file():
        leaderboard = project.leaderboard.read_text(encoding="utf-8")
    return {
        "root": str(project.root),
        "project": {
            "name": project_front.get("project") or project.root.name,
            "front": project_front,
            "sections": project_sections,
        },
        "goals": goals,
        "experiments": experiments,
        "proposals": proposals,
        "runs": model["runs"],
        "leaderboard": leaderboard,
        "table_path": project.rel(project.table_path) if project.table_path.is_file() else None,
        "warnings": model["issues"],
    }
