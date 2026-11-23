"""Materialize private Obsidian graph edges from committed Talos evidence and source."""
import ast
import hashlib
import json
import re
import tempfile
from pathlib import Path

from talos_core.self_improvement.graph_review import source_groups
from talos_core.memory.memory_connections import PROJECT, SOURCES
from talos_core.memory.memory_review import current_findings

MARKER = "<!-- talos-generated-graph -->"
ROOT = Path(__file__).resolve().parents[2]


def write_note(path, body, *, managed=False):
    """Atomically replace changed UTF-8 notes without disturbing unchanged mtimes."""
    if managed and path.is_symlink():
        return False
    try:
        previous = path.read_text(encoding="utf-8")
        if previous == body or (managed and not previous.startswith(MARKER + "\n")):
            return False
    except FileNotFoundError:
        pass
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        temporary = Path(tmp.name)
        try:
            tmp.write(body)
            tmp.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def link(section, name):
    return f"[[Talos maps/{section}/{name}]]"


def sync_notes(folder, notes):
    folder.mkdir(mode=0o700, exist_ok=True)
    changed = 0
    for name, body in notes.items():
        path = folder / (name + ".md")
        changed += write_note(path, MARKER + "\n" + body.rstrip() + "\n", managed=True)
    for path in folder.glob("*.md"):
        if path.stem not in notes and not path.is_symlink():
            with path.open(encoding="utf-8") as stream:
                owned = stream.readline().rstrip() == MARKER
            if owned:
                path.unlink()
                changed += 1
    return changed


def evidence_name(app, source, record):
    key = json.dumps((app, source, record), ensure_ascii=False).encode()
    return "Evidence " + hashlib.sha256(key).hexdigest()[:24]


def publish_connections(db, folder, enabled):
    allowed = set(SOURCES) | enabled
    projects, evidence, findings, groups = {}, {}, {}, {}

    def evidence_link(app, source, record):
        name = evidence_name(app, source, record)
        evidence[name] = f"# Indexed evidence\n\nSource · [[Talos maps/{app} index|{app}]]\n"
        return link("Evidence", name)

    if db and db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_connections'").fetchone():
        for project, app, source, record in db.execute(
                "SELECT project,app,source,record FROM memory_connections ORDER BY project,app,source,record"):
            if app in allowed and PROJECT.fullmatch(project):
                projects.setdefault(project, []).append(evidence_link(app, source, record))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='reviewed_project_pairs'").fetchone():
            evidence_sets = {project: set(edges) for project, edges in projects.items()}
            for a, b in db.execute("SELECT a,b FROM reviewed_project_pairs ORDER BY a,b"):
                if a in evidence_sets and b in evidence_sets and len(evidence_sets[a] & evidence_sets[b]) >= 2:
                    projects[a].append(link("Projects", b))
                    projects[b].append(link("Projects", a))
        for group in source_groups(db, allowed, verified=True):
            title = "Cross-source evidence " + hashlib.sha256(group.project.encode()).hexdigest()[:20]
            descriptions = group.description()
            group_links = [evidence_link(*proof) for proof in group.proofs]
            groups[title] = (f"# Cross-source project evidence\n\n{link('Projects',group.project)} has "
                             f"{len(group_links)} selected indexed records across {descriptions['families']} "
                             "source families.\n\n" + "\n".join("- " + edge for edge in group_links))
            projects[group.project].append(link("Evidence groups",title))
        for project, edges in projects.items():
            for ident, finding in current_findings(db, project, allowed):
                title = f"Finding {project} {ident}"
                proof_links = [evidence_link(app, source, record) for _, app, source, record, _ in finding["proofs"]]
                # Render model text literally so only validated citations create graph edges.
                findings[title] = ("# AI hypothesis\n\n    " + " ".join(finding["claim"].split()) + "\n\n"
                                   + "\n".join("- " + edge for edge in proof_links))
                edges.append(link("Findings", title))
    sections = {key: f"# {key}\n\n" + "\n".join("- " + edge for edge in edges) for key, edges in projects.items()}
    changes = sum(sync_notes(folder / name, notes) for name, notes in
                  (("Evidence", evidence), ("Evidence groups", groups), ("Findings", findings), ("Projects", sections)))
    body = "# Project connections\n\n" + "".join(f"- {link('Projects', key)}\n" for key in projects)
    changes += write_note(folder / "Project connections.md", body)
    return changes


def source_files(root):
    for directory in (root, root / "tools", root / "mac", root / "talos_core", root / "talos_core/scheduling"):
        for path in directory.glob("*"):
            if path.suffix in (".py", ".swift", ".ts", ".mjs") and path.is_file() and not path.is_symlink():
                yield path


def code_name(relative):
    return relative.replace("/", " ") + " source"


def read_module(path, root):
    raw = path.read_bytes()
    imports = set()
    if path.suffix == ".py":
        try:
            tree = ast.parse(raw)
        except (SyntaxError, ValueError):
            tree = None
        for node in ast.walk(tree) if tree else ():
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
                imports.update(node.module + "." + alias.name for alias in node.names)
    elif path.suffix in (".ts", ".mjs"):
        for name in re.findall(rb"""(?m)^import[^;]*?from\s+["']\./([\w-]+)\.(?:ts|mjs)["']""", raw):
            key = path.parent.relative_to(root) / name.decode("ascii")
            imports.add(".".join(key.parts))
    return hashlib.sha256(raw).hexdigest(), imports


def publish_code(root, folder, stamps, cache):
    modules = {p.relative_to(root).with_suffix("").as_posix().replace("/", "."): p for p in stamps}
    notes = {}
    for path, signature in stamps.items():
        if path not in cache or cache[path][0] != signature:
            cache[path] = (signature, *read_module(path, root))
        _, digest, imports = cache[path]
        relative = path.relative_to(root).as_posix()
        dependencies = sorted(code_name(modules[name].relative_to(root).as_posix()) for name in imports if name in modules)
        notes[code_name(relative)] = (f"# {relative}\n\nContent SHA-256 · `{digest}`\n\n"
                                      + "\n".join("- " + link("Code", name) for name in dependencies))
    for path in cache.keys() - stamps.keys():
        del cache[path]
    changed = sync_notes(folder / "Code", notes)
    if not (folder / "Recent source edits.md").exists():
        write_note(folder / "Recent source edits.md", "# Recent source edits\n\n")
    body = "# Code index\n\n[[Talos maps/Recent source edits]]\n\n" + "".join(f"- {link('Code', name)}\n" for name in sorted(notes))
    return changed + write_note(folder / "Code index.md", body)
