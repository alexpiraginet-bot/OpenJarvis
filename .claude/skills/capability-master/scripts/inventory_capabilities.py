#!/usr/bin/env python3
"""Inventory every capability reachable from the current session.

Sweeps four surfaces — installed skills, attached MCP servers, in-repo
registries, and runtime CLIs/dependencies — and prints a ranked report.

Standard library only, so it runs anywhere a Python 3.8+ interpreter exists
without a virtualenv or an install step.

Usage
-----
    python3 inventory_capabilities.py --environment all
    python3 inventory_capabilities.py --environment all --mode hard --out report.md
    python3 inventory_capabilities.py --mode auto --task "enviar whatsapp" --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

MODES = ("leve", "auto", "hard")
ENVIRONMENTS = ("local", "repo", "mcp", "all")

#: Directories that may hold agent skills, in precedence order.
SKILL_ROOTS = (
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
    Path.cwd() / ".claude" / "skills",
)

#: Files that may declare MCP servers.
MCP_CONFIGS = (
    Path.home() / ".claude.json",
    Path.cwd() / ".mcp.json",
    Path.cwd() / ".claude" / "mcp.json",
)

#: Registry decorators worth knowing about. Extend via references/.
REGISTRY_PATTERN = re.compile(
    r"@(?P<registry>\w*Registry)\.register(?:_value)?\(\s*[\"'](?P<key>[^\"']+)[\"']"
)

#: Directories never worth walking.
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "site-packages",
}

#: CLIs whose presence changes what the agent can do.
RUNTIME_TOOLS = (
    "git",
    "gh",
    "uv",
    "python3",
    "node",
    "npm",
    "bun",
    "docker",
    "ffmpeg",
    "rg",
    "jq",
    "psql",
    "sqlite3",
    "ollama",
)

#: Keyword → surface hints used to rank providers against a task.
TASK_HINTS = {
    "whatsapp": ("whatsapp", "twilio", "baileys", "sendblue"),
    "email": ("gmail", "outlook", "imap", "email", "smtp"),
    "calendar": ("calendar", "gcalendar", "agenda", "event"),
    "planilha": ("xlsx", "sheet", "csv", "excel"),
    "spreadsheet": ("xlsx", "sheet", "csv", "excel"),
    "pdf": ("pdf",),
    "extrato": ("bank", "extrato", "financ", "ofx", "csv"),
    "banco": ("bank", "sql", "postgres", "sqlite", "supabase"),
    "notion": ("notion",),
    "design": ("figma", "canva", "design"),
    "deploy": ("vercel", "netlify", "docker", "deploy"),
    "treino": ("strava", "health", "oura", "fitness", "workout"),
    "saude": ("health", "oura", "strava"),
}


@dataclass
class Capability:
    """One thing the agent can call."""

    name: str
    surface: str
    provider: str = ""
    description: str = ""
    location: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# -- Skills ------------------------------------------------------------------


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """Extract simple ``key: value`` pairs from a YAML frontmatter block."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: Dict[str, str] = {}
    key = ""
    for raw in text[3:end].splitlines():
        if not raw.strip():
            continue
        if raw[:1] in (" ", "\t") and key:
            # Continuation of a wrapped value.
            fields[key] += " " + raw.strip()
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def scan_skills(mode: str) -> List[Capability]:
    """Find installed agent skills across every known skill root."""
    found: List[Capability] = []
    seen = set()
    for root in SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            name = skill_md.parent.name
            if name in seen:
                continue
            seen.add(name)
            description = ""
            try:
                meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                description = meta.get("description", "")
                name = meta.get("name", name)
            except OSError:
                description = "(unreadable)"
            extra: Dict[str, Any] = {}
            if mode == "hard":
                extra["scripts"] = sorted(
                    p.name for p in (skill_md.parent / "scripts").glob("*")
                ) or []
                extra["references"] = sorted(
                    p.name for p in (skill_md.parent / "references").glob("*")
                ) or []
            found.append(
                Capability(
                    name=name,
                    surface="skill",
                    provider=str(root),
                    description=description,
                    location=str(skill_md),
                    extra=extra,
                )
            )
    return found


# -- MCP ---------------------------------------------------------------------


def _collect_mcp(node: Any, out: Dict[str, Any]) -> None:
    """Walk a config tree collecting every ``mcpServers`` mapping found."""
    if isinstance(node, dict):
        servers = node.get("mcpServers")
        if isinstance(servers, dict):
            out.update(servers)
        for value in node.values():
            _collect_mcp(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_mcp(value, out)


def scan_mcp(mode: str) -> List[Capability]:
    """Find MCP servers declared in any reachable config."""
    servers: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    for config in MCP_CONFIGS:
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        before = set(servers)
        _collect_mcp(data, servers)
        for key in set(servers) - before:
            sources[key] = str(config)

    found = []
    for name, spec in sorted(servers.items()):
        transport = "stdio"
        detail = ""
        if isinstance(spec, dict):
            if spec.get("url"):
                transport = str(spec.get("type") or "http")
                detail = str(spec["url"])
            elif spec.get("command"):
                detail = " ".join(
                    [str(spec["command"]), *(str(a) for a in spec.get("args", []))]
                )
        extra = {"transport": transport}
        if mode == "hard":
            extra["spec"] = spec if isinstance(spec, dict) else {}
        found.append(
            Capability(
                name=name,
                surface="mcp",
                provider=transport,
                description=detail[:160],
                location=sources.get(name, ""),
                extra=extra,
            )
        )
    return found


# -- Repo --------------------------------------------------------------------


def _iter_source_files(root: Path, limit: int) -> Iterable[Path]:
    """Yield python source files under ``root``, skipping vendored trees."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            count += 1
            if count > limit:
                return
            yield Path(dirpath) / filename


def scan_repo(root: Path, mode: str) -> List[Capability]:
    """Find registry-registered capabilities inside a checkout.

    In ``leve`` mode this only counts directories that look like capability
    packages. From ``auto`` upward it parses source for registry decorators,
    which is what actually enumerates connectors, channels, tools and agents.
    """
    if not root.is_dir():
        return []

    found: List[Capability] = []

    if mode == "leve":
        for candidate in ("connectors", "channels", "tools", "agents", "skills"):
            for path in root.rglob(candidate):
                if not path.is_dir() or any(p in SKIP_DIRS for p in path.parts):
                    continue
                modules = [
                    p.stem
                    for p in path.glob("*.py")
                    if not p.stem.startswith("_")
                ]
                if modules:
                    found.append(
                        Capability(
                            name=candidate,
                            surface="repo",
                            provider="package",
                            description=f"{len(modules)} módulos",
                            location=str(path),
                            extra={"modules": sorted(modules)},
                        )
                    )
        return found

    file_limit = 20000 if mode == "hard" else 4000
    for source in _iter_source_files(root, file_limit):
        try:
            text = source.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "Registry.register" not in text:
            continue
        for match in REGISTRY_PATTERN.finditer(text):
            found.append(
                Capability(
                    name=match.group("key"),
                    surface="repo",
                    provider=match.group("registry"),
                    location=str(source.relative_to(root))
                    if source.is_relative_to(root)
                    else str(source),
                )
            )
    return found


def scan_dependencies(root: Path) -> List[Capability]:
    """Read declared dependency groups — hard mode only."""
    found: List[Capability] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            text = pyproject.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for match in re.finditer(
            r"^(?P<name>[a-zA-Z0-9_-]+)\s*=\s*\[", text, re.MULTILINE
        ):
            found.append(
                Capability(
                    name=match.group("name"),
                    surface="dependency",
                    provider="pyproject.optional-dependencies",
                    location=str(pyproject.name),
                )
            )
    package_json = root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for dep in sorted(data.get("dependencies", {})):
            found.append(
                Capability(name=dep, surface="dependency", provider="npm")
            )
    return found


# -- Runtime -----------------------------------------------------------------


def scan_runtime() -> List[Capability]:
    """Detect which useful CLIs are actually on PATH."""
    return [
        Capability(name=tool, surface="runtime", location=path)
        for tool in RUNTIME_TOOLS
        if (path := shutil.which(tool))
    ]


# -- Analysis ----------------------------------------------------------------


def rank_for_task(task: str, capabilities: Sequence[Capability]) -> List[Capability]:
    """Score capabilities against a free-text task description."""
    if not task:
        return []
    lowered = task.lower()
    keywords = {word for word in re.findall(r"[a-zà-ÿ]{4,}", lowered)}
    for trigger, hints in TASK_HINTS.items():
        if trigger in lowered:
            keywords.update(hints)

    scored = []
    for capability in capabilities:
        haystack = f"{capability.name} {capability.description}".lower()
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score:
            scored.append((score, capability))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [capability for _, capability in scored[:12]]


def find_gaps(task: str, matches: Sequence[Capability]) -> List[str]:
    """Report task keywords that no capability appears to serve."""
    if not task:
        return []
    gaps = []
    lowered = task.lower()
    for trigger, hints in TASK_HINTS.items():
        if trigger not in lowered:
            continue
        blob = " ".join(f"{m.name} {m.description}".lower() for m in matches)
        if not any(hint in blob for hint in hints):
            gaps.append(trigger)
    return gaps


def find_overlaps(capabilities: Sequence[Capability]) -> Dict[str, List[str]]:
    """Group capabilities that seem to do the same job."""
    buckets: Dict[str, List[str]] = {}
    for topic, hints in TASK_HINTS.items():
        providers = sorted(
            {
                f"{c.surface}:{c.name}"
                for c in capabilities
                if any(hint in c.name.lower() for hint in hints)
            }
        )
        if len(providers) > 1:
            buckets[topic] = providers
    return buckets


# -- Reporting ---------------------------------------------------------------


def render(
    groups: Dict[str, List[Capability]],
    *,
    mode: str,
    task: str,
    matches: Sequence[Capability],
    gaps: Sequence[str],
    overlaps: Dict[str, List[str]],
) -> str:
    """Render the human-readable report."""
    lines: List[str] = []
    total = sum(len(items) for items in groups.values())
    lines.append(f"# Capability inventory — modo {mode}")
    lines.append("")
    lines.append(f"{total} capacidades em {len(groups)} superfícies.")
    lines.append("")

    for surface, items in groups.items():
        if not items:
            lines.append(f"## {surface} — vazio")
            if surface == "mcp":
                # Being precise matters here: a remote/hosted session gets its
                # MCP servers injected by the harness at runtime, so they exist
                # for the agent while appearing nowhere on disk. Reporting a
                # bare "vazio" would read as "none attached", which is wrong.
                lines.append("")
                lines.append(
                    "> Nenhum servidor MCP **declarado em arquivo de config**. "
                    "Sessões hospedadas recebem MCPs injetados em runtime, que "
                    "um scan de disco não enxerga — confira a lista de tools da "
                    "sessão antes de concluir que não há nenhum."
                )
            lines.append("")
            continue
        lines.append(f"## {surface} ({len(items)})")
        lines.append("")
        if surface == "repo":
            by_registry: Dict[str, List[str]] = {}
            for item in items:
                by_registry.setdefault(item.provider, []).append(item.name)
            for registry, names in sorted(by_registry.items()):
                unique = sorted(set(names))
                shown = ", ".join(unique[:14])
                more = f" … +{len(unique) - 14}" if len(unique) > 14 else ""
                lines.append(f"- **{registry}** ({len(unique)}): {shown}{more}")
        else:
            for item in sorted(items, key=lambda c: c.name):
                detail = item.description[:96]
                lines.append(f"- **{item.name}**" + (f" — {detail}" if detail else ""))
        lines.append("")

    if task:
        lines.append(f"## Melhores candidatos para: {task!r}")
        lines.append("")
        if matches:
            for item in matches:
                lines.append(f"- `{item.surface}` **{item.name}** ({item.provider})")
        else:
            lines.append("- nenhum candidato encontrado")
        lines.append("")
        lines.append("## GAPS")
        lines.append("")
        if gaps:
            for gap in gaps:
                lines.append(f"- **{gap}** — nada em escopo cobre isso; é aqui que")
                lines.append("  código novo se justifica")
        else:
            lines.append("- nenhum: tudo que a tarefa pede já existe em escopo")
        lines.append("")

    if overlaps:
        lines.append("## OVERLAPS")
        lines.append("")
        for topic, providers in sorted(overlaps.items()):
            lines.append(f"- **{topic}**: {', '.join(providers)}")
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=ENVIRONMENTS, default="all")
    parser.add_argument("--mode", choices=MODES, default="auto")
    parser.add_argument("--repo", default=str(Path.cwd()))
    parser.add_argument("--task", default="", help="Task to rank capabilities for")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--out", default="", help="Also write the report here")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).expanduser().resolve()
    want = (
        {"local", "repo", "mcp"}
        if args.environment == "all"
        else {args.environment}
    )

    groups: Dict[str, List[Capability]] = {}
    if "local" in want:
        groups["skills"] = scan_skills(args.mode)
        groups["runtime"] = scan_runtime()
    if "mcp" in want:
        groups["mcp"] = scan_mcp(args.mode)
    if "repo" in want:
        groups["repo"] = scan_repo(repo_root, args.mode)
        if args.mode == "hard":
            groups["dependency"] = scan_dependencies(repo_root)

    everything = [item for items in groups.values() for item in items]
    matches = rank_for_task(args.task, everything)
    gaps = find_gaps(args.task, matches)
    overlaps = find_overlaps(everything) if args.mode == "hard" else {}

    if args.json:
        payload = {
            "mode": args.mode,
            "environment": args.environment,
            "repo": str(repo_root),
            "capabilities": {
                surface: [asdict(item) for item in items]
                for surface, items in groups.items()
            },
            "matches": [asdict(item) for item in matches],
            "gaps": list(gaps),
            "overlaps": overlaps,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    report = render(
        groups,
        mode=args.mode,
        task=args.task,
        matches=matches,
        gaps=gaps,
        overlaps=overlaps,
    )
    print(report)
    if args.out:
        try:
            Path(args.out).write_text(report, encoding="utf-8")
            print(f"\n→ relatório salvo em {args.out}", file=sys.stderr)
        except OSError as exc:
            print(f"não consegui escrever {args.out}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
