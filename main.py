#!/usr/bin/env python3
"""Skill Manager — discover, subscribe, browse, compare, delete skills."""

import difflib
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Skill Manager", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------------------
# Known scan roots (places likely to contain SKILL.md)
# ---------------------------------------------------------------------------
SCAN_ROOTS = [
    "~/.hermes/skills",
    "~/.copilot/skills",
    "~/.claude/skills",
    "~/.cursor/skills",
    "~/.config/opencode/skills",
    "~/.codex/skills",
    "~/.agents/skills",
    "~/.gemini/skills",
    "~/.codeium/windsurf/skills",
    "~/.openclaw/skills",
    "~/.trae/skills",
    "~/.codebuddy/skills",
    "~/.factory/skills",
    "~/.config/agents/skills",
    "~/.kilocode/skills",
    "~/.mux/skills",
    "~/.qoder/skills",
    "~/.qwen/skills",
    "~/.zencoder/skills",
    "~/.crush/skills",
    "~/.pi/agent/skills",
    "~/.kilocode/skills",
]

USER_CONFIG_FILE = Path.home() / ".skill-manager" / "config.json"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SkillInfo(BaseModel):
    name: str
    root_dir: str          # the subscribed root directory label
    skill_dir: str         # full path to skill directory
    skill_md_path: str
    description: str
    tags: list[str]
    category: Optional[str]
    file_count: int


class DiscoveredDir(BaseModel):
    path: str
    label: str
    skill_count: int
    subscribed: bool


class DiffResult(BaseModel):
    skill_a: dict
    skill_b: dict
    unified_diff: str
    a_lines: int
    b_lines: int
    added: int
    removed: int


class DeleteResult(BaseModel):
    success: bool
    skill_name: str
    deleted_path: str
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def exp(p: str) -> str:
    return os.path.expanduser(p)


def load_config() -> dict:
    if USER_CONFIG_FILE.exists():
        try:
            return json.loads(USER_CONFIG_FILE.read_text())
        except Exception:
            pass
    return {"scan_roots": [], "subscriptions": []}


def save_config(cfg: dict):
    USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def get_all_roots() -> list[str]:
    """Combine default + user-configured scan roots."""
    cfg = load_config()
    roots = list(SCAN_ROOTS) + cfg.get("scan_roots", [])
    seen = set()
    result = []
    for r in roots:
        e = exp(r)
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def get_subscriptions() -> list[str]:
    return load_config().get("subscriptions", [])


def set_subscriptions(paths: list[str]):
    cfg = load_config()
    cfg["subscriptions"] = paths
    save_config(cfg)


def parse_frontmatter(content: str) -> dict:
    meta = {"description": "", "tags": [], "category": None}
    if not content.startswith("---"):
        return meta
    parts = content.split("---", 2)
    if len(parts) < 3:
        return meta
    for m in re.finditer(r"(?m)^(description|tags|category):\s*(.+)$", parts[1]):
        key, val = m.group(1), m.group(2).strip()
        if key == "tags":
            meta["tags"] = [t.strip().strip("'\"") for t in val.strip("[]").split(",") if t.strip()]
        else:
            meta[key] = val.strip("'\"")
    return meta


def discover_skill_dirs(root: str, max_depth: int = 4) -> list[str]:
    """Find all directories containing SKILL.md under root."""
    found = []
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        if depth > max_depth:
            dirnames.clear()
            continue
        if "SKILL.md" in filenames:
            found.append(dirpath)
    return sorted(found)


def scan_skill(skill_dir: str, root_label: str) -> SkillInfo:
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""
    meta = parse_frontmatter(content)
    file_count = 0
    for _, _, files in os.walk(skill_dir):
        file_count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".pyc"))
    return SkillInfo(
        name=os.path.basename(skill_dir),
        root_dir=root_label,
        skill_dir=skill_dir,
        skill_md_path=skill_md_path,
        description=meta["description"],
        tags=meta["tags"],
        category=meta.get("category"),
        file_count=file_count,
    )


def read_file_content(path: str) -> str:
    p = path if os.path.isabs(path) else exp(path)
    if not os.path.isfile(p):
        raise HTTPException(404, f"File not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/discover")
async def discover():
    """Scan all roots, return directories found with SKILL.md, grouped by root."""
    roots = get_all_roots()
    subs = set(get_subscriptions())
    results = []
    seen_dirs = set()
    for root in roots:
        label = root.replace(os.path.expanduser("~"), "~")
        dirs = discover_skill_dirs(root)
        for d in dirs:
            if d not in seen_dirs:
                seen_dirs.add(d)
                results.append(DiscoveredDir(
                    path=d,
                    label=label,
                    skill_count=1,  # each dir is one skill
                    subscribed=d in subs,
                ))
    return results


@app.get("/api/subscriptions")
async def get_subs():
    """Return currently subscribed directories with their skills."""
    subs = get_subscriptions()
    roots = get_all_roots()
    # Map each subscribed path back to its root label
    root_label_map = {}
    for root in roots:
        for d in discover_skill_dirs(root):
            if d in subs:
                label = root.replace(os.path.expanduser("~"), "~")
                root_label_map[d] = label
    skills = []
    for d in subs:
        if os.path.isdir(d):
            label = root_label_map.get(d, os.path.dirname(d))
            skills.append(scan_skill(d, label))
    return skills


@app.post("/api/subscriptions")
async def subscribe(path: str):
    subs = get_subscriptions()
    p = exp(path) if not os.path.isabs(path) else path
    if p not in subs:
        subs.append(p)
        set_subscriptions(subs)
    return {"success": True}


@app.delete("/api/subscriptions")
async def unsubscribe(path: str):
    subs = get_subscriptions()
    p = exp(path) if not os.path.isabs(path) else path
    subs = [s for s in subs if s != p]
    set_subscriptions(subs)
    return {"success": True}


@app.post("/api/subscriptions/batch")
async def batch_subscribe(paths: list[str]):
    subs = set(get_subscriptions())
    for p in paths:
        e = exp(p) if not os.path.isabs(p) else p
        subs.add(e)
    set_subscriptions(sorted(subs))
    return {"success": True, "count": len(subs)}


@app.get("/api/skills")
async def list_skills(search: Optional[str] = None):
    skills = []
    subs = get_subscriptions()
    roots = get_all_roots()
    root_label_map = {}
    for root in roots:
        for d in discover_skill_dirs(root):
            if d in subs:
                label = root.replace(os.path.expanduser("~"), "~")
                root_label_map[d] = label
    for d in subs:
        if os.path.isdir(d):
            label = root_label_map.get(d, "~")
            skills.append(scan_skill(d, label))
    if search:
        s = search.lower()
        skills = [sk for sk in skills
                  if s in sk.name.lower() or s in sk.description.lower()
                  or any(s in t.lower() for t in sk.tags)]
    # Group by name
    groups = defaultdict(list)
    for sk in skills:
        groups[sk.name].append(sk)
    result = []
    for name in sorted(groups.keys(), key=lambda n: (-len(groups[n]), n)):
        result.append({"name": name, "instances": [s.model_dump() for s in groups[name]]})
    return result


@app.get("/api/skill-content")
async def skill_content(path: str):
    content = read_file_content(path)
    return {"path": path, "content": content}


@app.get("/api/diff")
async def diff_skills(path_a: str, path_b: str):
    content_a = read_file_content(path_a)
    content_b = read_file_content(path_b)
    lines_a = content_a.splitlines(keepends=True)
    lines_b = content_b.splitlines(keepends=True)
    diff = difflib.unified_diff(lines_a, lines_b, fromfile="a", tofile="b", lineterm="")
    diff_text = "".join(diff)
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    added = removed = 0
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "insert": added += b2 - b1
        elif op == "replace": added += b2 - b1; removed += a2 - a1
        elif op == "delete": removed += a2 - a1
    def skill_info(p):
        subs = get_subscriptions()
        roots = get_all_roots()
        for root in roots:
            for sk_dir in discover_skill_dirs(root):
                if sk_dir == p:
                    sk = scan_skill(sk_dir, root.replace(os.path.expanduser("~"), "~"))
                    return {"name": sk.name, "root_dir": sk.root_dir, "skill_dir": sk.skill_dir}
        return {"name": os.path.basename(p), "root_dir": "?", "skill_dir": p}
    return DiffResult(
        skill_a=skill_info(path_a),
        skill_b=skill_info(path_b),
        unified_diff=diff_text,
        a_lines=len(lines_a),
        b_lines=len(lines_b),
        added=added,
        removed=removed,
    )


@app.delete("/api/skills/delete")
async def delete_skill(path: str):
    p = exp(path) if not os.path.isabs(path) else path
    if not os.path.isdir(p):
        raise HTTPException(404, f"Not found: {path}")
    name = os.path.basename(p)
    shutil.rmtree(p)
    # Also remove from subscriptions
    subs = [s for s in get_subscriptions() if s != p]
    set_subscriptions(subs)
    return DeleteResult(success=True, skill_name=name, deleted_path=p, message=f"Deleted '{name}'")


@app.post("/api/scan-roots")
async def add_scan_root(path: str):
    cfg = load_config()
    e = exp(path)
    roots = cfg.get("scan_roots", [])
    if e not in roots:
        roots.append(path)
        cfg["scan_roots"] = roots
        save_config(cfg)
    return {"success": True}


@app.delete("/api/scan-roots")
async def remove_scan_root(path: str):
    cfg = load_config()
    e = exp(path)
    cfg["scan_roots"] = [r for r in cfg.get("scan_roots", []) if exp(r) != e]
    save_config(cfg)
    return {"success": True}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)


@app.get("/")
async def index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(html_path):
        return HTMLResponse(content=open(html_path).read())
    return HTMLResponse(content="<h1>Skill Manager</h1><p>Put index.html in static/</p>")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8900)
