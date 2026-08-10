# Registry patterns the scanner recognises

The repo scan looks for decorator-based registration, which is how most
agent frameworks make components discoverable at runtime. The pattern it
matches is:

```python
@SomethingRegistry.register("key")
class Something: ...

SomethingRegistry.register_value("key", Something)
```

Concretely, the regex is:

```
@(?P<registry>\w*Registry)\.register(?:_value)?\(\s*["'](?P<key>[^"']+)["']
```

So any class whose name ends in `Registry` is picked up automatically — a new
`PaymentRegistry` needs no change to the scanner.

## What it deliberately does not catch

- **Dynamic keys.** `@ToolRegistry.register(name)` where `name` is a variable
  cannot be resolved without executing the module, and importing arbitrary repo
  code to enumerate it is not worth the blast radius. These show up in output as
  the literal variable name (e.g. `name`) — treat such entries as noise.
- **Entry points.** `[project.entry-points]` plugins in `pyproject.toml` are
  listed under the `dependency` surface in `hard` mode, not resolved.
- **Non-Python registries.** TypeScript/Rust registries are not scanned. For a
  TS project, the `package.json` dependency list in `hard` mode is the closest
  proxy.

## Teaching it a new shape

Edit `REGISTRY_PATTERN` in `scripts/inventory_capabilities.py`. Keep it a
regex over source text rather than an import: the scanner must stay safe to run
against a checkout it has never seen, and importing untrusted repo code to list
its features is the opposite of that.

## Surfaces and what "empty" means

| Surface | Empty means |
|---|---|
| `skills` | No `SKILL.md` under any known skill root |
| `mcp` | No server **declared in a config file** — a hosted session's injected servers are invisible to a disk scan |
| `repo` | No registry decorators found (or the path is not a checkout) |
| `runtime` | None of the probed CLIs are on `PATH` |
| `dependency` | No `pyproject.toml` / `package.json` at the repo root |

Never round an empty surface up to "this capability does not exist" — round it
to "this scan did not find it here".
