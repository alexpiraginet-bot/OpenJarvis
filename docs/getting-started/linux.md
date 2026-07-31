# Linux Install

```bash
curl -fsSL https://open-jarvis.github.io/OpenJarvis/install.sh | bash
```

Tested on: Ubuntu 22.04 / 24.04, Fedora 40, Debian 12, Arch.

## Prerequisites

Most distros ship `git` and `curl`. If yours doesn't:

```bash
# Debian / Ubuntu
sudo apt install git curl

# Fedora / RHEL
sudo dnf install git curl

# Arch
sudo pacman -S git curl
```

Ollama's Linux build ships as a `.tar.zst` archive, so installing it also needs
`zstd`. The installer bootstraps that for you via your package manager (it only
needs `sudo` to already be authenticated — see the note below); you don't have
to install it up front.

!!! note "Minimal images and passwordless sudo"
    On a container or minimal image without `zstd`, the installer runs
    `sudo apt-get install -y zstd` (or the dnf/pacman/zypper/apk equivalent)
    before handing off to Ollama. Because stdin is occupied by the `curl` pipe,
    sudo can't prompt for a password — run `sudo -v` first, or pre-install
    `zstd`, if your sudo isn't already authenticated.

## NVIDIA / AMD GPU

The installer auto-detects via `nvidia-smi` / `rocm-smi`. Datacenter cards (A100, H100, MI300+) get vLLM as the recommended engine; consumer cards get Ollama (NVIDIA) or Lemonade (AMD).

## See also

- [Full installer reference](install.md)
