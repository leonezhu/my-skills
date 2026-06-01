#!/usr/bin/env python3
"""Skill Manager — discover roots, subscribe, browse, compare, delete skills."""

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

app = FastAPI(title="Skill Manager", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------------------
# Known scan roots — each is a parent directory that may contain SKILL.md
# ---------------------------------------------------------------------------
SCAN_ROOTS = [
    ("Hermes Agent",    "~/.hermes/skills"),
    ("GitHub Copilot",  "~/.copilot/skills"),
    ("Claude Code",     "~/.claude/skills"),
    ("Cursor",          "~/.cursor/skills"),
    ("OpenCode",        "~/.config/opencode/skills"),
    ("Codex",           "~/.codex/skills"),
    ("Cline",           "~/.agents/skills"),
    ("Gemini CLI",      "~/.gemini/skills"),
    ("Windsurf",        "~/.codeium/windsurf/skills"),
    ("OpenClaw",        "~/.openclaw/skills"),
    ("Trae",            "~/.trae/skills"),
    ("CodeBuddy",       "~/.codebuddy/skills"),
    ("Droid",           "~/.factory/skills"),
    ("Amp",             "~/.config/agents/skills"),
    ("Kilo Code",       "~/.kilocode/skills"),
    ("Mux",             "~/.mux/skills"),
    ("Qoder",           "~/.qoder/skills"),
    ("Qwen Code",       "~/.qwen/skills"),
    ("Zencoder",        "~/.zencoder/skills"),
    ("Crush",           "~/.crush/skills"),
    ("Pi",              "~/.pi/agent/skills"),
]

USER_CONFIG_FILE = Path.home() / ".skill-manager" / "config.json"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class RootInfo(BaseModel):
    label: str
    path: str
    expanded_path: str
    exists: bool
    skill_count: int
    subscribed: bool
    sample_skills: list[str]   # first few skill names


class SkillInfo(BaseModel):
    name: str
    root_dir: str
    skill_dir: str
    skill_md_path: str
    description: str
    tags: list[str]
    category: Optional[str]
    file_count: int


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
# Config
# ---------------------------------------------------------------------------
def exp(p: str) -> str:
    return os.path.expanduser(p)


def load_config() -> dict:
    if USER_CONFIG_FILE.exists():
        try:
            return json.loads(USER_CONFIG_FILE.read_text())
        except Exception:
            pass
    return {"extra_roots": [], "subscriptions": []}


def save_config(cfg: dict):
    USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def get_all_roots() -> list[tuple[str, str]]:
    """Return (label, expanded_path) for all scan roots."""
    cfg = load_config()
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for label, path in SCAN_ROOTS:
        e = exp(path)
        if e not in seen:
            seen.add(e)
            result.append((label, e))
    for item in cfg.get("extra_roots", []):
        label = item.get("label", "")
        path = exp(item.get("path", ""))
        if path not in seen:
            seen.add(path)
            result.append((label, path))
    return result


def get_subscribed_roots() -> list[str]:
    """Subscribed root expanded paths."""
    return load_config().get("subscriptions", [])


def set_subscribed_roots(paths: list[str]):
    cfg = load_config()
    cfg["subscriptions"] = paths
    save_config(cfg)


# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------
def discover_skill_dirs(root: str, max_depth: int = 5) -> list[str]:
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


def scan_skill(skill_dir: str, root_label: str) -> SkillInfo:
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""
    meta = parse_frontmatter(content)
    file_count = sum(
        1 for _, _, files in os.walk(skill_dir)
        for f in files
        if not f.startswith(".") and not f.endswith(".pyc")
    )
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


def get_all_skills_from_roots(root_paths: list[str]) -> list[SkillInfo]:
    """Scan subscribed roots and return all skills."""
    root_map = {exp(p): label for label, p in get_all_roots()}
    skills = []
    for rp in root_paths:
        label = root_map.get(rp, "~")
        for sd in discover_skill_dirs(rp):
            skills.append(scan_skill(sd, label))
    return skills


# ---------------------------------------------------------------------------
# API — Discover (root-level)
# ---------------------------------------------------------------------------
@app.get("/api/discover")
async def discover():
    """Return all known roots with skill counts and subscription status."""
    roots = get_all_roots()
    subs = set(get_subscribed_roots())
    results = []
    for label, expanded in roots:
        dirs = discover_skill_dirs(expanded)
        names = [os.path.basename(d) for d in dirs[:8]]
        results.append(RootInfo(
            label=label,
            path=expanded.replace(os.path.expanduser("~"), "~"),
            expanded_path=expanded,
            exists=os.path.isdir(expanded),
            skill_count=len(dirs),
            subscribed=expanded in subs,
            sample_skills=names,
        ))
    return results


# ---------------------------------------------------------------------------
# API — Subscriptions (root-level)
# ---------------------------------------------------------------------------
@app.post("/api/subscriptions")
async def subscribe(path: str):
    subs = set(get_subscribed_roots())
    p = exp(path)
    subs.add(p)
    set_subscribed_roots(sorted(subs))
    return {"success": True}


@app.delete("/api/subscriptions")
async def unsubscribe(path: str):
    subs = set(get_subscribed_roots())
    p = exp(path)
    subs.discard(p)
    set_subscribed_roots(sorted(subs))
    return {"success": True}


@app.get("/api/subscriptions")
async def list_subs():
    """Return subscribed roots and their skills."""
    subs = get_subscribed_roots()
    root_map = {exp(p): label for label, p in get_all_roots()}
    result = []
    for rp in subs:
        label = root_map.get(rp, rp.replace(os.path.expanduser("~"), "~"))
        dirs = discover_skill_dirs(rp)
        result.append({
            "label": label,
            "path": rp.replace(os.path.expanduser("~"), "~"),
            "skills": [scan_skill(d, label).model_dump() for d in dirs],
        })
    return result


# ---------------------------------------------------------------------------
# API — Skills (from subscribed roots)
# ---------------------------------------------------------------------------
@app.get("/api/skills")
async def list_skills(search: Optional[str] = None, root: Optional[str] = None):
    subs = get_subscribed_roots()
    if root:
        subs = [rp for rp in subs if rp.replace(os.path.expanduser("~"), "~") == root
                or rp == exp(root)]
    skills = get_all_skills_from_roots(subs)
    if search:
        s = search.lower()
        skills = [sk for sk in skills
                  if s in sk.name.lower() or s in sk.description.lower()
                  or any(s in t.lower() for t in sk.tags)]
    groups: dict[str, list[SkillInfo]] = defaultdict(list)
    for sk in skills:
        groups[sk.name].append(sk)
    result = []
    for name in sorted(groups, key=lambda n: (-len(groups[n]), n)):
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

    def info(p):
        root_map = {exp(rp2): lab for lab, rp2 in get_all_roots()}
        for rp in get_subscribed_roots():
            for sd in discover_skill_dirs(rp):
                if sd == p:
                    sk = scan_skill(sd, root_map.get(rp, "~"))
                    return {"name": sk.name, "root_dir": sk.root_dir, "skill_dir": sk.skill_dir}
        return {"name": os.path.basename(p), "root_dir": "?", "skill_dir": p}

    return DiffResult(
        skill_a=info(path_a), skill_b=info(path_b),
        unified_diff=diff_text, a_lines=len(lines_a), b_lines=len(lines_b),
        added=added, removed=removed,
    )


@app.delete("/api/skills/delete")
async def delete_skill(path: str):
    p = exp(path) if not os.path.isabs(path) else path
    if not os.path.isdir(p):
        raise HTTPException(404, f"Not found: {path}")
    name = os.path.basename(p)
    shutil.rmtree(p)
    return DeleteResult(success=True, skill_name=name, deleted_path=p, message=f"Deleted '{name}'")


# ---------------------------------------------------------------------------
# API — Extra roots (user-customizable scan paths)
# ---------------------------------------------------------------------------
@app.post("/api/extra-roots")
async def add_extra_root(label: str, path: str):
    cfg = load_config()
    e = exp(path)
    existing = cfg.get("extra_roots", [])
    if not any(exp(r.get("path", "")) == e for r in existing):
        existing.append({"label": label, "path": path})
        cfg["extra_roots"] = existing
        save_config(cfg)
    return {"success": True}


@app.delete("/api/extra-roots")
async def remove_extra_root(path: str):
    cfg = load_config()
    e = exp(path)
    cfg["extra_roots"] = [r for r in cfg.get("extra_roots", []) if exp(r.get("path", "")) != e]
    save_config(cfg)
    return {"success": True}


@app.get("/api/extra-roots")
async def list_extra_roots():
    return load_config().get("extra_roots", [])


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
    return HTMLResponse(content="<h1>Skill Manager</h1>")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8900)
