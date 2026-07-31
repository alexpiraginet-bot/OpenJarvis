# Failure: zstd missing when Ollama is installed

## Trigger

`zstd` is not installed, and the installer reaches the "Install Ollama" step
(i.e. `ollama` is not already on PATH).

Ollama now publishes its Linux builds as `.tar.zst` and its install script
hard-requires the `zstd` binary to unpack them:

```
ERROR: This version requires zstd for extraction. Please install zstd and try again
```

`zstd` is not part of a base Debian/Ubuntu, Fedora or Alpine install, and is
absent from most container base images — so this hits any minimal Linux host.

Left unhandled this is a bad failure: it lands at step 6 of 12, *after* the
repo clone, the venv and the editable install have already succeeded, so the
user is left with `~/.openjarvis/src` and `~/.openjarvis/.venv` on disk but no
`config.toml`, no `jarvis` symlink and no PATH entry — and the only error they
see is in Ollama's voice, never mentioning OpenJarvis or how to resume.

## Expected behavior

- Before handing off to Ollama's installer, `install.sh` bootstraps `zstd`
  itself via the system package manager (the same dispatch used for `git` and
  `curl`: apt-get / dnf / yum / pacman / zypper / apk).
- The bootstrap is best-effort and never fatal on its own — Ollama still falls
  back to `.tgz` for older versions, so an install that could still succeed is
  not pre-emptively failed.
- If `zstd` can't be installed (no passwordless sudo, unsupported package
  manager, non-Linux host), the installer warns **in its own voice** with the
  exact command to run and notes that re-running resumes from where it stopped.
- Skipped entirely when `ollama` is already on PATH — an existing Ollama
  install must not drag in a package-manager call.

## Retry

Install `zstd` per the printed hint, then re-run the curl line. Completed
steps are recorded in `~/.openjarvis/.state/install-state.json` and skipped, so
the re-run picks up at the Ollama step:

```bash
sudo apt-get install -y zstd   # or: dnf / pacman / zypper / apk
curl -fsSL https://open-jarvis.github.io/OpenJarvis/install.sh | bash
```

## Test

- `tests/install/bash/test_install.bats::"installs zstd before handing off to the Ollama installer"`
- `tests/install/bash/test_install.bats::"warns but still hands off when zstd can't be installed"`
- `tests/install/bash/test_install.bats::"skips the zstd bootstrap when ollama is already installed"`
