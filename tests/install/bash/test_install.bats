#!/usr/bin/env bats

setup() {
    TEST_TMPDIR=$(mktemp -d)
    export FAKE_HOME="$TEST_TMPDIR/home"
    mkdir -p "$FAKE_HOME"
    export HOME="$FAKE_HOME"
    export OPENJARVIS_HOME="$FAKE_HOME/.openjarvis"

    export STUBS_DIR="$BATS_TEST_DIRNAME/stubs"
    # Use a copy of stubs without uv-style real binaries that might be on the host.
    # Just put our stubs first in PATH.
    export PATH="$STUBS_DIR:/usr/bin:/bin"

    export GIT_STUB_LOG="$TEST_TMPDIR/git.log"
    export OLLAMA_STUB_LOG="$TEST_TMPDIR/ollama.log"
    export UV_STUB_LOG="$TEST_TMPDIR/uv.log"
    export CURL_STUB_LOG="$TEST_TMPDIR/curl.log"
    : > "$GIT_STUB_LOG"
    : > "$OLLAMA_STUB_LOG"
    : > "$UV_STUB_LOG"
    : > "$CURL_STUB_LOG"

    # uv stub needs to fake creating a venv with a jarvis binary.
    # Replace the stubs/uv with one that creates the venv tree on `venv` command.
    # Since we don't want to mutate the real stub, override via a per-test stub dir.
    export PER_TEST_STUBS="$TEST_TMPDIR/stubs"
    mkdir -p "$PER_TEST_STUBS"
    cat "$STUBS_DIR/uv" > "$PER_TEST_STUBS/uv"
    chmod +x "$PER_TEST_STUBS/uv"
    # Wrap uv to handle 'venv' specially.
    cat > "$PER_TEST_STUBS/uv" <<EOF
#!/usr/bin/env bash
echo "\$@" >> "\$UV_STUB_LOG"
case "\$1" in
    venv)
        # uv venv [--python X.Y] <path>
        # Find the path arg (last non-flag).
        for a in "\$@"; do
            if [[ "\$a" != --* ]] && [[ "\$a" != -* ]] && [[ "\$a" != "venv" ]] && [[ ! "\$a" =~ ^[0-9]+\\.[0-9]+\$ ]]; then
                venv_path="\$a"
            fi
        done
        if [[ -n "\$venv_path" ]]; then
            mkdir -p "\$venv_path/bin"
            cat > "\$venv_path/bin/jarvis" <<'EOJ'
#!/usr/bin/env bash
# fake jarvis for tests
echo "fake jarvis: \$@"
exit 0
EOJ
            chmod +x "\$venv_path/bin/jarvis"
            cat > "\$venv_path/bin/python" <<'EOJ'
#!/usr/bin/env bash
# fake python that prints empty for the inline embedded scripts (recommend_model, etc.)
exit 0
EOJ
            chmod +x "\$venv_path/bin/python"
        fi
        ;;
esac
exit 0
EOF
    chmod +x "$PER_TEST_STUBS/uv"

    # Copy other stubs, with the per-test uv first.
    cp "$STUBS_DIR"/{git,curl,ollama,cargo,rustup} "$PER_TEST_STUBS/"
    chmod +x "$PER_TEST_STUBS"/*

    export PATH="$PER_TEST_STUBS:/usr/bin:/bin"

    export SCRIPT="$BATS_TEST_DIRNAME/../../../scripts/install/install.sh"
}

teardown() {
    [[ -n "${TEST_TMPDIR:-}" ]] && rm -rf "$TEST_TMPDIR"
}

@test "refuses to run as root" {
    # EUID is readonly in bash, so we stub `id` to return uid 0 instead.
    local root_stubs="$TEST_TMPDIR/root_stubs"
    mkdir -p "$root_stubs"
    cp "$PER_TEST_STUBS"/* "$root_stubs/"
    cat > "$root_stubs/id" <<'IDEOF'
#!/usr/bin/env bash
if [[ "$1" == "-u" ]]; then echo 0; else echo "uid=0(root) gid=0(root) groups=0(root)"; fi
IDEOF
    chmod +x "$root_stubs/id"
    PATH="$root_stubs:/usr/bin:/bin" run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -ne 0 ]
    echo "$output" | grep -qi "root"
}

@test "creates ~/.openjarvis directory tree" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    [ -d "$OPENJARVIS_HOME/src" ]
    [ -d "$OPENJARVIS_HOME/.state" ]
    [ -d "$OPENJARVIS_HOME/.scripts" ]
}

@test "writes install-state.json on success" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    [ -f "$OPENJARVIS_HOME/.state/install-state.json" ]
}

@test "calls git clone for the OpenJarvis repo" {
    run bash "$SCRIPT" --no-bg-orchestrator
    grep -q "clone" "$GIT_STUB_LOG"
}

@test "creates jarvis symlink in ~/.local/bin" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    [ -L "$FAKE_HOME/.local/bin/jarvis" ] || [ -f "$FAKE_HOME/.local/bin/jarvis" ]
}

@test "is idempotent — second run skips completed steps" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    : > "$GIT_STUB_LOG"
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    ! grep -q "clone" "$GIT_STUB_LOG"
}

@test "detects WSL2 and writes platform note to install-state" {
    OPENJARVIS_FORCE_WSL=1 run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    grep -q "wsl" "$OPENJARVIS_HOME/.state/install-state.json"
}

@test "fails loudly when git is missing" {
    # Build a minimal stubs dir that has curl/uv/ollama but NO git.
    # We must NOT include /usr/bin or /bin in PATH because those dirs have real
    # git binaries on this system. Instead, provide only the commands the script
    # needs before it reaches the `need git` check (id, cat, bash).
    local no_git_stubs="$TEST_TMPDIR/no_git_stubs"
    mkdir -p "$no_git_stubs"
    for f in curl uv ollama cargo rustup; do
        cp "$PER_TEST_STUBS/$f" "$no_git_stubs/"
        chmod +x "$no_git_stubs/$f"
    done
    # Provide minimal system utilities (no git) using explicit symlinks.
    cat > "$no_git_stubs/id" <<'IDEOF'
#!/bin/bash
echo "1000"
IDEOF
    chmod +x "$no_git_stubs/id"
    ln -sf /bin/cat "$no_git_stubs/cat"
    ln -sf /bin/bash "$no_git_stubs/bash"
    PATH="$no_git_stubs" run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -ne 0 ]
    echo "$output" | grep -qi "git"
}

@test "--force re-runs completed steps" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    : > "$GIT_STUB_LOG"
    run bash "$SCRIPT" --no-bg-orchestrator --force
    [ "$status" -eq 0 ]
    # With --force, second run re-clones (clone is in git.log again).
    grep -q "clone" "$GIT_STUB_LOG"
}

@test "--minimal skips foreground model pull" {
    run bash "$SCRIPT" --no-bg-orchestrator --minimal
    [ "$status" -eq 0 ]
    # Ollama 'pull' should NOT have been called.
    if [ -s "$OLLAMA_STUB_LOG" ]; then
        ! grep -q "^pull " "$OLLAMA_STUB_LOG"
    fi
}

@test "install succeeds with no system Python on PATH (#484)" {
    # Build a stubs dir with NO python3 / python — verifies the
    # bootstrap shims (get_anon_id, beacon, mark_done) are Python-free.
    local no_py_stubs="$TEST_TMPDIR/no_py_stubs"
    mkdir -p "$no_py_stubs"
    for f in git curl uv ollama cargo rustup; do
        cp "$PER_TEST_STUBS/$f" "$no_py_stubs/"
        chmod +x "$no_py_stubs/$f"
    done
    # Provide minimal core utilities the shims need (od, sed, awk, tr, grep).
    for util in id cat bash sh head tail awk sed grep od tr cut mv rm mkdir cp chmod ln test pgrep date sleep ps; do
        if [[ -x "/usr/bin/$util" ]]; then
            ln -sf "/usr/bin/$util" "$no_py_stubs/$util"
        elif [[ -x "/bin/$util" ]]; then
            ln -sf "/bin/$util" "$no_py_stubs/$util"
        fi
    done
    # pgrep may be absent on minimal BusyBox/Alpine hosts; stub it as
    # "no matching process" so start_ollama's existing-daemon check
    # falls through to the spawn path (which the ollama stub handles).
    if [[ ! -x "$no_py_stubs/pgrep" ]]; then
        printf '#!/bin/sh\nexit 1\n' > "$no_py_stubs/pgrep"
        chmod +x "$no_py_stubs/pgrep"
    fi
    PATH="$no_py_stubs" run bash "$SCRIPT" --no-bg-orchestrator --minimal
    [ "$status" -eq 0 ]
    # State file was still written (mark_done is Python-free).
    [ -f "$OPENJARVIS_HOME/.state/install-state.json" ]
    # And it still has valid-looking JSON for state_done's grep.
    grep -q '"install_uv":' "$OPENJARVIS_HOME/.state/install-state.json"
}

@test "mark_done is idempotent — second mark of same key doesn't duplicate" {
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    # Capture first state file
    local first_count
    first_count="$(grep -c '"install_uv":' "$OPENJARVIS_HOME/.state/install-state.json")"
    [ "$first_count" -eq 1 ]
    # Second run reuses state — keys should not duplicate even if some path re-marks.
    run bash "$SCRIPT" --no-bg-orchestrator
    [ "$status" -eq 0 ]
    local second_count
    second_count="$(grep -c '"install_uv":' "$OPENJARVIS_HOME/.state/install-state.json")"
    [ "$second_count" -eq 1 ]
}

@test "create_venv picks newest in requires-python range, not hardcoded 3.11 (#476)" {
    run bash "$SCRIPT" --no-bg-orchestrator --minimal
    [ "$status" -eq 0 ]
    # uv stub logs every invocation. We should see `venv --python 3.13 ...`
    # (the upper bound of >=3.10,<3.14 is 3.14, so the highest usable is 3.13).
    grep -q '^venv --python 3.13' "$UV_STUB_LOG"
}

# --- zstd bootstrap for the Ollama handoff -------------------------------
#
# Ollama ships its Linux builds as .tar.zst and aborts with "This version
# requires zstd for extraction" when the `zstd` binary is missing. zstd is
# not part of a base Debian/Ubuntu/Fedora install, so install.sh has to
# bootstrap it before handing off — otherwise the install dies after the
# venv is built, leaving no config.toml and no `jarvis` on PATH.

# Build a stub PATH with NO ollama and NO zstd, plus sudo + apt-get stubs.
# $1 — when "provides-zstd", the apt-get stub actually drops a zstd stub on
# PATH (simulating a successful package install); otherwise it is a no-op
# (simulating a package manager that can't supply it).
_setup_no_ollama_stubs() {
    local apt_behavior="$1"
    NO_OLLAMA_STUBS="$TEST_TMPDIR/no_ollama_stubs"
    mkdir -p "$NO_OLLAMA_STUBS"
    for f in git curl uv cargo rustup; do
        cp "$PER_TEST_STUBS/$f" "$NO_OLLAMA_STUBS/"
    done

    export APT_STUB_LOG="$TEST_TMPDIR/apt.log"
    : > "$APT_STUB_LOG"
    {
        echo '#!/usr/bin/env bash'
        echo 'echo "$@" >> "$APT_STUB_LOG"'
        if [[ "$apt_behavior" == "provides-zstd" ]]; then
            echo "if [[ \" \$* \" == *\" zstd \"* ]]; then"
            echo "    printf '%s\\n' '#!/usr/bin/env bash' 'exit 0' > '$NO_OLLAMA_STUBS/zstd'"
            echo "    chmod +x '$NO_OLLAMA_STUBS/zstd'"
            echo "fi"
        fi
        echo 'exit 0'
    } > "$NO_OLLAMA_STUBS/apt-get"

    # `sudo -n true` is the non-interactive auth probe; anything else is
    # the wrapped command and just runs through.
    cat > "$NO_OLLAMA_STUBS/sudo" <<'SUDOEOF'
#!/usr/bin/env bash
[[ "$1" == "-n" ]] && exit 0
exec "$@"
SUDOEOF

    # What `curl -fsSL https://ollama.com/install.sh | sh` executes. Like
    # the real installer, it puts an `ollama` binary on PATH.
    cat > "$TEST_TMPDIR/ollama-install.sh" <<OLLEOF
cp "$STUBS_DIR/ollama" "$NO_OLLAMA_STUBS/ollama"
chmod +x "$NO_OLLAMA_STUBS/ollama"
OLLEOF
    export CURL_STUB_OUTPUT_FILE="$TEST_TMPDIR/ollama-install.sh"

    chmod +x "$NO_OLLAMA_STUBS"/*
}

@test "installs zstd before handing off to the Ollama installer" {
    _setup_no_ollama_stubs provides-zstd
    PATH="$NO_OLLAMA_STUBS:/usr/bin:/bin" run bash "$SCRIPT" --no-bg-orchestrator --minimal
    [ "$status" -eq 0 ]
    # The package manager was asked for zstd, before Ollama's installer ran.
    grep -q "install -y zstd" "$APT_STUB_LOG"
    # zstd was obtained, so no fallback warning should be printed.
    ! echo "$output" | grep -q "could not be installed automatically"
}

@test "warns but still hands off when zstd can't be installed" {
    _setup_no_ollama_stubs no-op
    PATH="$NO_OLLAMA_STUBS:/usr/bin:/bin" run bash "$SCRIPT" --no-bg-orchestrator --minimal
    # A missing zstd is not fatal on its own — Ollama still falls back to
    # .tgz for older versions, so the install must carry on to completion.
    [ "$status" -eq 0 ]
    echo "$output" | grep -q "zstd"
    echo "$output" | grep -q "could not be installed automatically"
    # And the advice has to be actionable, in OpenJarvis's own voice.
    echo "$output" | grep -q "apt-get install -y zstd"
    echo "$output" | grep -q "re-run this installer"
}

@test "skips the zstd bootstrap when ollama is already installed" {
    # PER_TEST_STUBS has an ollama stub, so install_ollama returns early
    # and must not touch the package manager at all.
    export APT_STUB_LOG="$TEST_TMPDIR/apt.log"
    : > "$APT_STUB_LOG"
    cat > "$PER_TEST_STUBS/apt-get" <<'APTEOF'
#!/usr/bin/env bash
echo "$@" >> "$APT_STUB_LOG"
exit 0
APTEOF
    chmod +x "$PER_TEST_STUBS/apt-get"
    run bash "$SCRIPT" --no-bg-orchestrator --minimal
    [ "$status" -eq 0 ]
    [ ! -s "$APT_STUB_LOG" ]
}
