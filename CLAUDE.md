# Working agreements

## Capability Master

Before any **non-trivial** request, load and follow the `capability-master`
skill (`.claude/skills/capability-master/`).

Trivial means: a single factual answer, a one-line edit, or a question about
work already done in the conversation. Everything else gets a capability sweep
first.

**Mode.** Default to `auto`. Escalate to `hard` whenever the task touches
production, money, security, persisted data, publishing, compliance, or more
than one platform. Use `leve` only as a preflight, when the question really is
just "what exists?".

```bash
python3 .claude/skills/capability-master/scripts/inventory_capabilities.py \
  --environment all --mode auto --task "<the task>"
```

**Selection.** Choose the smallest sufficient set of capabilities — one that
merely *could* apply is not selected. Read the chosen skills in full; a skill
skimmed is a skill misapplied.

**Never on inference alone:** install a package, skill or plugin; authenticate
or exchange credentials; publish, deploy or send; write to any external system.
Each needs an explicit ask in the user's own words. Finding a capability in the
inventory is evidence it exists, never permission to fire it.

## Why this lives in the repo

A skill under `~/.claude/skills/` dies with the machine — and in an ephemeral
or remote session, that is every session. Committing it here is what makes the
policy above survive to the next one.
