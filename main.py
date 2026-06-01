#!/usr/bin/env python3
"""Skill Manager Backend — scan, diff, delete skills across Agent directories."""

import asyncio
import difflib
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Skill Manager", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Known agent skill directories (global only)
# ---------------------------------------------------------------------------
DEFAULT_DIRECTORIES = [
    ("Hermes Agent", "~/.hermes/skills"),
    ("GitHub Copilot", "~/.copilot/skills"),
    ("Claude Code", "~/.claude/skills"),
    ("Cursor", "~/.cursor/skills"),
    ("OpenCode", "~/.config/opencode/skills"),
    ("Codex", "~/.codex/skills"),
    ("Cline", "~/.agents/skills"),
    ("Gemini CLI", "~/.gemini/skills"),
    ("Windsurf", "~/.codeium/windsurf/skills"),
    ("OpenClaw", "~/.openclaw/skills"),
    ("Trae", "~/.trae/skills"),
    ("CodeBuddy", "~/.codebuddy/skills"),
    ("Droid", "~/.factory/skills"),
    ("Amp", "~/.config/agents/skills"),
    ("Kilo Code", "~/.kilocode/skills"),
    ("Mux", "~/.mux/skills"),
    ("Qoder", "~/.qoder/skills"),
    ("Qwen Code", "~/.qwen/skills"),
    ("Zencoder", "~/.zencoder/skills"),
    ("Kimi CLI", "~/.config/agents/skills"),
]

# User config file
USER_CONFIG_FILE = Path.home() / ".skill-manager" / "directories.json"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SkillInfo(BaseModel):
    name: str
    agent: str
    directory: str
    full_path: str
    skill_md_path: str
    description: str
    tags: list[str]
    category: Optional[str]
    file_count: int
    file_list: list[str]


class DirectoryInfo(BaseModel):
    label: str
    path: str
    exists: bool
    skill_count: int


class DiffResult(BaseModel):
    skill_a: SkillInfo
    skill_b: SkillInfo
    unified_diff: str
    a_lines: int
    b_lines: int
    added: int
    removed: int
    changed: int


class DeleteResult(BaseModel):
    success: bool
    skill_name: str
    agent: str
    deleted_path: str
    message: str


class UserDirectory(BaseModel):
    label: str
    path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def expand_path(p: str) -> str:
    return os.path.expanduser(p)


def get_all_directories() -> list[tuple[str, str]]:
    """Return list of (label, expanded_path) from defaults + user config."""
    dirs = [(label, expand_path(path)) for label, path in DEFAULT_DIRECTORIES]
    # Load user config
    if USER_CONFIG_FILE.exists():
        import json
        try:
            data = json.loads(USER_CONFIG_FILE.read_text())
            for item in data.get("directories", []):
                label = item.get("label", "")
                path = expand_path(item.get("path", ""))
                dirs.append((label, path))
        except Exception:
            pass
    return dirs


def parse_skill_md(content: str) -> dict:
    """Extract frontmatter fields from SKILL.md content."""
    meta = {"description": "", "tags": [], "category": None}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_block = parts[1]
            for m in re.finditer(r"(?m)^(description|tags|category):\s*(.+)$", yaml_block):
                key, val = m.group(1), m.group(2).strip()
                if key == "tags":
                    meta["tags"] = [t.strip().strip("'\"") for t in val.strip("[]").split(",") if t.strip()]
                else:
                    meta[key] = val.strip("'\"")
    return meta


def scan_skill_directory(dir_path: str, agent_label: str) -> list[SkillInfo]:
    """Scan a directory recursively for SKILL.md files."""
    skills = []
    if not os.path.isdir(dir_path):
        return skills
    for root, dirs, files in os.walk(dir_path):
        if "SKILL.md" in files:
            skill_md_path = os.path.join(root, "SKILL.md")
            try:
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                content = ""
            meta = parse_skill_md(content)
            name = os.path.basename(root)
            # Count files (non-hidden, non-pycache)
            file_list = []
            for fr, _, ffiles in os.walk(root):
                rel = os.path.relpath(fr, root)
                for ff in ffiles:
                    if ff.startswith(".") or ff.endswith(".pyc"):
                        continue
                    fp = os.path.join(rel, ff) if rel != "." else ff
                    file_list.append(fp)
            skills.append(SkillInfo(
                name=name,
                agent=agent_label,
                directory=dir_path,
                full_path=root,
                skill_md_path=skill_md_path,
                description=meta["description"],
                tags=meta["tags"],
                category=meta.get("category"),
                file_count=len(file_list),
                file_list=file_list[:20],  # cap display
            ))
    return skills


def read_skill_md(skill: SkillInfo) -> str:
    """Read SKILL.md content from a skill."""
    try:
        with open(skill.skill_md_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------
@app.get("/api/directories")
async def list_directories():
    dirs = get_all_directories()
    results = []
    seen_paths = set()
    for label, path in dirs:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        exists = os.path.isdir(path)
        count = len(scan_skill_directory(path, label)) if exists else 0
        results.append(DirectoryInfo(label=label, path=path, exists=exists, skill_count=count))
    return results


@app.post("/api/directories")
async def add_directory(dir_info: UserDirectory):
    USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json
    data = {"directories": []}
    if USER_CONFIG_FILE.exists():
        try:
            data = json.loads(USER_CONFIG_FILE.read_text())
        except Exception:
            pass
    data.setdefault("directories", []).append({"label": dir_info.label, "path": dir_info.path})
    USER_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return {"success": True, "message": f"Added directory: {dir_info.label}"}


@app.delete("/api/directories")
async def remove_directory(label: str, path: str):
    USER_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json
    if not USER_CONFIG_FILE.exists():
        raise HTTPException(404, "No user directories configured")
    data = json.loads(USER_CONFIG_FILE.read_text())
    original = data.get("directories", [])
    data["directories"] = [d for d in original if not (d.get("label") == label and d.get("path") == path)]
    USER_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return {"success": True, "message": f"Removed directory: {label}"}


@app.get("/api/skills")
async def list_skills(agent: Optional[str] = None, search: Optional[str] = None):
    dirs = get_all_directories()
    all_skills = []
    seen_paths = set()
    for label, path in dirs:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        if agent and label != agent:
            continue
        all_skills.extend(scan_skill_directory(path, label))
    if search:
        s = search.lower()
        all_skills = [sk for sk in all_skills if s in sk.name.lower() or s in sk.description.lower() or any(s in t.lower() for t in sk.tags)]
    return all_skills


@app.get("/api/skills/grouped")
async def list_skills_grouped(search: Optional[str] = None):
    dirs = get_all_directories()
    all_skills = []
    seen_paths = set()
    for label, path in dirs:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        all_skills.extend(scan_skill_directory(path, label))
    if search:
        s = search.lower()
        all_skills = [sk for sk in all_skills if s in sk.name.lower() or s in sk.description.lower() or any(s in t.lower() for t in sk.tags)]
    # Group by skill name (find duplicates)
    from collections import defaultdict
    groups = defaultdict(list)
    for sk in all_skills:
        groups[sk.name].append(sk)
    # Sort: groups with duplicates first (more agents = first)
    result = []
    for name in sorted(groups.keys(), key=lambda n: (-len(groups[n]), n)):
        result.append({"name": name, "instances": groups[name]})
    return result


@app.get("/api/skills/{skill_md_path:path}")
async def read_skill_content(skill_md_path: str):
    full = expand_path(skill_md_path) if not os.path.isabs(skill_md_path) else skill_md_path
    if not os.path.isfile(full):
        raise HTTPException(404, "SKILL.md not found")
    with open(full, "r", encoding="utf-8") as f:
        return {"path": full, "content": f.read()}


@app.get("/api/diff")
async def diff_skills(path_a: str, path_b: str):
    """Generate unified diff between two SKILL.md files."""
    content_a = read_skill_md_content(path_a)
    content_b = read_skill_md_content(path_b)
    lines_a = content_a.splitlines(keepends=True)
    lines_b = content_b.splitlines(keepends=True)
    diff = difflib.unified_diff(lines_a, lines_b, fromfile=f"a/{path_a}", tofile=f"b/{path_b}", lineterm="")
    diff_text = "".join(diff)
    # Count changes
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
    added = 0
    removed = 0
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "insert":
            added += b2 - b1
        elif op == "replace":
            added += b2 - b1
            removed += a2 - a1
        elif op == "delete":
            removed += a2 - a1
    # Find skill infos
    dirs = get_all_directories()
    skill_a = find_skill_info(path_a, dirs)
    skill_b = find_skill_info(path_b, dirs)
    return DiffResult(
        skill_a=skill_a,
        skill_b=skill_b,
        unified_diff=diff_text,
        a_lines=len(lines_a),
        b_lines=len(lines_b),
        added=added,
        removed=removed,
        changed=added + removed,
    )


def read_skill_md_content(path: str) -> str:
    full = path if os.path.isabs(path) else expand_path(path)
    if not os.path.isfile(full):
        raise HTTPException(404, f"File not found: {path}")
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def find_skill_info(skill_md_path: str, dirs: list) -> SkillInfo:
    for label, dir_path in dirs:
        skills = scan_skill_directory(dir_path, label)
        for sk in skills:
            if sk.skill_md_path == skill_md_path or sk.skill_md_path == os.path.abspath(skill_md_path):
                return sk
    name = os.path.basename(os.path.dirname(skill_md_path))
    return SkillInfo(
        name=name, agent="unknown", directory="unknown",
        full_path=os.path.dirname(skill_md_path), skill_md_path=skill_md_path,
        description="", tags=[], category=None, file_count=0, file_list=[]
    )


@app.delete("/api/skills/delete")
async def delete_skill(path: str):
    """Delete a skill directory."""
    full = path if os.path.isabs(path) else expand_path(path)
    if not os.path.isdir(full):
        raise HTTPException(404, f"Skill directory not found: {path}")
    name = os.path.basename(full)
    # Find which agent
    dirs = get_all_directories()
    agent = "unknown"
    for label, dir_path in dirs:
        if full.startswith(dir_path):
            agent = label
            break
    shutil.rmtree(full)
    return DeleteResult(
        success=True,
        skill_name=name,
        agent=agent,
        deleted_path=full,
        message=f"Deleted skill '{name}' from {agent}",
    )


# ---------------------------------------------------------------------------
# Serve static frontend
# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

@app.get("/")
async def index():
    html_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Skill Manager</h1><p>Frontend not built. Put index.html in static/</p>")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8900)
