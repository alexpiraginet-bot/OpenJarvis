---
name: capability-master
description: Inventory, rank and orchestrate every capability reachable from the current session — skills, MCP servers, connectors, channels, agents, tools and registries — across the local environment and any git repository in scope. Use when the user asks what capabilities/skills/connectors are available, asks to map or audit the tooling of a repo, asks which integration to use for a task, mentions capability-master, or asks to work in "modo leve", "modo auto" or "modo hard". Also use before large integration work to discover what already exists instead of rebuilding it.
---

# Capability Master

## Overview

Most wasted engineering in an agent-driven codebase is rebuilding something the
environment already had. A repo ships 39 connectors and someone writes a 40th by
hand; a session has a Notion MCP server attached and someone scrapes HTML.

Capability Master is the antidote: a single sweep that answers **"what can I
actually call right now, and what is the best thing to call for this task?"**

It covers four surfaces:

| Surface | Where it lives | Examples |
|---|---|---|
| **Skills** | `~/.claude/skills`, `~/.codex/skills`, `<repo>/.claude/skills` | pdf, xlsx, mcp-builder |
| **MCP servers** | `~/.claude.json`, `.mcp.json` | Notion, Supabase, Figma, GitHub |
| **Repo registries** | source tree | `@ToolRegistry.register`, `ChannelRegistry`, connectors |
| **Runtime** | installed packages, CLIs | `uv`, `gh`, `ffmpeg`, python deps |

## When to run at all

Before any **non-trivial** request. Trivial means: a single factual answer, a
one-line edit, or a question about work already done in this conversation.
Everything else gets a sweep first.

## Choosing the mode

Default to **auto**. Escalate to **hard** when the task touches any of:

| Trigger | Why hard |
|---|---|
| **Production** | A wrong provider choice ships to real users |
| **Money** | Payments, ledgers, pricing — errors are not reversible by a redeploy |
| **Security** | Auth, secrets, permissions, PII |
| **Persisted data** | Schemas and migrations outlive the decision that made them |
| **Publishing** | Anything that leaves the machine and cannot be unsent |
| **Compliance** | Regulated domains, audit trails, retention |
| **Multiple platforms** | Overlap detection is the whole point when two stacks must agree |

Use **leve** only when the answer is genuinely "what exists?" — a preflight,
not a decision.

## Choosing capabilities

Select the **smallest sufficient set**. A capability that merely *could* apply
is not selected; one the task cannot be completed without is. Then read the
chosen skills **in full** — a skill skimmed is a skill misapplied, and partial
reads are how instructions get inverted.

Never, on inference alone:

- install a package, skill or plugin
- authenticate, or exchange credentials
- publish, deploy or send
- write to any external system

Each of those needs an explicit ask in the user's own words. Finding a
capability in the inventory is evidence it exists, never permission to fire it.

## Modes

Pick the mode from the user's words. When unstated, apply the policy above.

### `leve` — light
Names only. Reads skill frontmatter and MCP config keys, no source parsing.
Use when the user just wants to know what exists, or as a fast preflight.
Typically < 2s.

```bash
python3 ~/.claude/skills/capability-master/scripts/inventory_capabilities.py \
  --environment all --mode leve
```

### `auto` — default
Everything in `leve`, plus repo registry scanning, gap detection and ranked
recommendations for the stated task. This is the right default for
"what should I use for X?".

```bash
python3 ~/.claude/skills/capability-master/scripts/inventory_capabilities.py \
  --environment all --mode auto --task "importar extratos bancários"
```

### `hard` — deep
Everything in `auto`, plus: full source scan for every registry decorator,
per-capability file locations, dependency extras from `pyproject.toml` /
`package.json`, duplicate/overlap detection, and a written report.

```bash
python3 ~/.claude/skills/capability-master/scripts/inventory_capabilities.py \
  --environment all --mode hard --out capability-report.md
```

> `hard` reads a lot of source. On a repo with thousands of files, pass
> `--repo <path>` to scope it rather than letting it walk everything.

## Workflow

1. **Run the inventory** at the mode the request implies. Always run it before
   claiming something does or does not exist — memory of a framework's feature
   set is not evidence about *this* checkout.
2. **Read the gaps section.** It lists capabilities the task needs that nothing
   in scope provides. That, and only that, is what deserves new code.
3. **Prefer in this order**: existing repo registry entry → attached MCP server
   → installed skill → new code. Each step down costs more to build and more to
   maintain.
4. **Report honestly.** If the inventory finds nothing for a surface, say the
   surface is empty rather than implying it was not checked. An empty MCP list
   means none are attached *to this session*, not that none exist.

## Reading the output

The script prints one section per surface, then:

- `GAPS` — requested capability with no provider found.
- `OVERLAPS` (hard only) — two or more providers for the same job. Overlap is
  not automatically a bug: a repo may deliberately ship both a Twilio and a
  Baileys WhatsApp path. Flag it, do not "fix" it unprompted.

Pass `--json` to get the same data machine-readably for further processing.

## Environments

`--environment` selects which surfaces to sweep:

- `local` — skills and CLIs on this machine
- `repo` — registries and dependencies in the checkout
- `mcp` — MCP servers attached to this session
- `all` — every surface (default)

## Companion references

- `references/registry-patterns.md` — the decorator and plugin shapes the
  scanner recognises, and how to teach it a new one.

## Anti-patterns

- **Do not** run `hard` on every turn. It is for audits and pre-integration
  planning, not routine questions.
- **Do not** treat the inventory as authorisation. Finding a connector does not
  mean credentials for it exist — check that separately before promising a user
  it works.
- **Do not** invent capabilities in the report. If the scanner did not find it,
  it is not in the report, even when you are confident the framework has it.
