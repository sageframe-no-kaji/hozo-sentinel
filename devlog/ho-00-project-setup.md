# Ho 0: Project Setup & Foundations

## HōzÅ (宝蔵) - "Treasure Storehouse"

**Duration:** ~2 hours
**Goal:** Set up a clean Python project with proper tooling from day one
**Learning Focus:** Git workflow, testing with pytest, code quality with black/flake8/mypy

---

## Why This Ho Exists

The Kanyō project grew organically - tooling was added later when the codebase was already complex. This time, we start with the foundation:

- **Git:** Clean commits, good messages, .gitignore done right
- **Testing:** pytest setup, first tests written BEFORE first code
- **Quality:** black, flake8, mypy configured from the start
- **Structure:** Simple, flat, easy to navigate

This Ho is about **building good habits**, not shipping features.

---

## Project Overview

### What HōzÅ Does

```
┌─────────────────────────────────────────────────────────────┐
│ YOUR SERVER (Docker Container)                              │
│                                                             │
│  HōzÅ Orchestrator:                                         │
│  1. Read job config (YAML)                                  │
│  2. Send Wake-on-LAN magic packet                           │
│  3. Wait for SSH to respond                                 │
│  4. Run syncoid (ZFS replication)                           │
│  5. Verify success                                          │
│  6. SSH: shutdown remote                                    │
│  7. Log result, send notification                           │
│                                                             │
│  Web UI: Status, manual trigger, logs                       │
└─────────────────────────────────────────────────────────────┘
                         │
                         │ WOL packet + SSH + Syncoid
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ REMOTE BACKUP BOX (Sleeping Mini-PC)                        │
│                                                             │
│  - Wakes on WOL                                             │
│  - Tailscale auto-connects                                  │
│  - SSH accepts connection                                   │
│  - ZFS receives data                                        │
│  - Shuts down when told                                     │
│                                                             │
│  No agent needed - just SSH + ZFS + Tailscale               │
└─────────────────────────────────────────────────────────────┘
```

### Project Structure (Target)

```
hozo/
├── src/hozo/                # Main package
│   ├── __init__.py
│   ├── core/                # Core orchestration logic
│   │   ├── __init__.py
│   │   ├── wol.py           # Wake-on-LAN
│   │   ├── ssh.py           # SSH connectivity & commands
│   │   ├── backup.py        # Syncoid wrapper
│   │   └── job.py           # Job execution orchestration
│   ├── config/              # Configuration handling
│   │   ├── __init__.py
│   │   └── loader.py        # YAML config loader
│   └── cli.py               # Command-line interface
├── tests/                   # Test suite
│   ├── __init__.py
│   ├── test_wol.py
│   ├── test_ssh.py
│   ├── test_backup.py
│   └── test_config.py
├── configs/                 # Example configurations
│   └── config.example.yaml
├── docs/                    # Documentation
│   └── architecture.md
├── devlog/                  # Development journal
│   └── ho-00-project-setup.md
├── .gitignore
├── .flake8
├── pyproject.toml           # Project config (black, mypy, pytest)
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
└── README.md
```

**Note:** Simpler than Kanyō. No `generation/`, no `detection/`, no Docker (yet). Just core functionality.

---

## Phase 1: Git Repository Setup

### 1.1 Create the Repository

```bash
# Create project directory
mkdir hozo
cd hozo

# Initialize git
git init

# Create initial structure
mkdir -p src/hozo/core src/hozo/config tests configs docs devlog
```

### 1.2 Create .gitignore

**Why this matters:** A good .gitignore prevents committing junk (venv, **pycache**, .env secrets).

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
venv/
ENV/
env/
.venv/

# IDE
.idea/
.vscode/
*.swp
*.swo
*~

# Testing
.pytest_cache/
.coverage
htmlcov/
.tox/
.nox/

# Type checking
.mypy_cache/

# Environment variables (secrets!)
.env
*.env.local

# OS
.DS_Store
Thumbs.db

# Project specific
logs/
*.log
```

### 1.3 First Commit

```bash
git add .gitignore
git commit -m "chore: initialize repository with .gitignore"
```

**Commit message convention:**

- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `test:` adding tests
- `chore:` maintenance (gitignore, config)
- `refactor:` code change that doesn't add feature or fix bug

---

## Phase 2: Python Project Configuration

### 2.1 Create pyproject.toml

This single file configures: project metadata, black, mypy, pytest, and isort.

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "hozo"
version = "0.1.0"
description = "Wake-on-demand ZFS backup orchestrator"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "you@example.com"}
]
keywords = ["zfs", "backup", "wake-on-lan", "syncoid"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: System Administrators",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
]

dependencies = [
    "wakeonlan>=3.1.0",
    "paramiko>=3.4.0",
    "pyyaml>=6.0",
    "click>=8.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=4.1.0",
    "black>=24.0.0",
    "flake8>=7.0.0",
    "mypy>=1.8.0",
    "isort>=5.13.0",
    "types-PyYAML>=6.0.0",
    "types-paramiko>=3.4.0",
]

[project.scripts]
hozo = "hozo.cli:main"

[project.urls]
Homepage = "https://github.com/yourusername/hozo"
Repository = "https://github.com/yourusername/hozo"

# ============================================
# Tool Configuration
# ============================================

[tool.setuptools.packages.find]
where = ["src"]

[tool.black]
line-length = 100
target-version = ["py310", "py311", "py312"]
include = '\.pyi?$'
exclude = '''
/(
    \.git
    | \.mypy_cache
    | \.pytest_cache
    | \.venv
    | venv
    | build
    | dist
)/
'''

[tool.isort]
profile = "black"
line_length = 100
skip = [".git", ".mypy_cache", ".pytest_cache", "venv", ".venv"]

[tool.mypy]
python_version = "3.10"
mypy_path = "src"
packages = ["hozo"]
strict = false
warn_return_any = true
warn_unused_configs = true
ignore_missing_imports = true
explicit_package_bases = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-v --tb=short"
```

### 2.2 Create .flake8

Flake8 doesn't support pyproject.toml yet, so it needs its own file:

```ini
[flake8]
max-line-length = 100
exclude =
    .git,
    __pycache__,
    .mypy_cache,
    .pytest_cache,
    venv,
    .venv,
    build,
    dist
ignore =
    # E203: whitespace before ':' (conflicts with black)
    E203,
    # W503: line break before binary operator (conflicts with black)
    W503
per-file-ignores =
    # F401: imported but unused (okay in __init__.py for re-exports)
    __init__.py:F401
```

### 2.3 Create Requirements Files

**requirements.txt** (production):

```txt
wakeonlan>=3.1.0
paramiko>=3.4.0
pyyaml>=6.0
click>=8.1.0
```

**requirements-dev.txt** (development):

```txt
-r requirements.txt
pytest>=8.0.0
pytest-cov>=4.1.0
black>=24.0.0
flake8>=7.0.0
mypy>=1.8.0
isort>=5.13.0
types-PyYAML>=6.0.0
types-paramiko>=3.4.0
```

### 2.4 Set Up Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate  # On Mac/Linux
# or: .\venv\Scripts\activate  # On Windows

# Install dev dependencies
pip install -r requirements-dev.txt

# Install package in editable mode
pip install -e .
```

### 2.5 Commit Configuration

```bash
git add pyproject.toml .flake8 requirements.txt requirements-dev.txt
git commit -m "chore: add Python project configuration"
```

---

## Phase 3: Create Package Structure

### 3.1 Create **init**.py Files

**src/hozo/**init**.py:**

```python
"""HōzÅ - Wake-on-demand ZFS backup orchestrator."""

__version__ = "0.1.0"
```

**src/hozo/core/**init**.py:**

```python
"""Core backup orchestration modules."""
```

**src/hozo/config/**init**.py:**

```python
"""Configuration handling."""
```

**tests/**init**.py:**

```python
"""HōzÅ test suite."""
```

### 3.2 Create Placeholder Modules

We'll create minimal placeholder files that pass linting. Real implementation comes in Ho 1.

**src/hozo/core/wol.py:**

```python
"""Wake-on-LAN functionality."""

from wakeonlan import send_magic_packet


def wake(mac_address: str, ip_address: str = "255.255.255.255", port: int = 9) -> bool:
    """
    Send a Wake-on-LAN magic packet to wake a remote machine.

    Args:
        mac_address: MAC address of the target machine (e.g., "AA:BB:CC:DD:EE:FF")
        ip_address: Broadcast IP address (default: 255.255.255.255)
        port: UDP port for WOL packet (default: 9)

    Returns:
        True if packet was sent successfully
    """
    send_magic_packet(mac_address, ip_address=ip_address, port=port)
    return True
```

**src/hozo/core/ssh.py:**

```python
"""SSH connectivity and remote command execution."""


def wait_for_ssh(host: str, port: int = 22, timeout: int = 120) -> bool:
    """
    Wait for SSH to become available on a remote host.

    Args:
        host: Hostname or IP address
        port: SSH port (default: 22)
        timeout: Maximum seconds to wait (default: 120)

    Returns:
        True if SSH is available, False if timeout
    """
    # TODO: Implement in Ho 1
    raise NotImplementedError("SSH polling not yet implemented")


def run_command(host: str, command: str, user: str = "root") -> tuple[int, str, str]:
    """
    Execute a command on a remote host via SSH.

    Args:
        host: Hostname or IP address
        command: Command to execute
        user: SSH user (default: root)

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    # TODO: Implement in Ho 1
    raise NotImplementedError("SSH command execution not yet implemented")
```

**src/hozo/core/backup.py:**

```python
"""Syncoid backup wrapper."""


def run_syncoid(
    source_dataset: str,
    target_host: str,
    target_dataset: str,
    recursive: bool = True,
) -> bool:
    """
    Run syncoid to replicate a ZFS dataset to a remote host.

    Args:
        source_dataset: Local ZFS dataset (e.g., "rpool/data")
        target_host: Remote hostname
        target_dataset: Remote ZFS dataset (e.g., "backup/rpool-data")
        recursive: Whether to replicate child datasets

    Returns:
        True if replication succeeded
    """
    # TODO: Implement in Ho 1
    raise NotImplementedError("Syncoid wrapper not yet implemented")
```

**src/hozo/core/job.py:**

```python
"""Job execution orchestration."""

from dataclasses import dataclass


@dataclass
class BackupJob:
    """Configuration for a backup job."""

    name: str
    source_dataset: str
    target_host: str
    target_dataset: str
    mac_address: str
    shutdown_after: bool = True
    timeout: int = 120


def run_job(job: BackupJob) -> bool:
    """
    Execute a complete backup job.

    Workflow:
        1. Send WOL packet
        2. Wait for SSH
        3. Run syncoid
        4. Shutdown remote (if configured)

    Args:
        job: Backup job configuration

    Returns:
        True if job completed successfully
    """
    # TODO: Implement in Ho 1
    raise NotImplementedError("Job execution not yet implemented")
```

**src/hozo/config/loader.py:**

```python
"""YAML configuration loader."""

from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path) -> dict[str, Any]:
    """
    Load configuration from a YAML file.

    Args:
        path: Path to the YAML config file

    Returns:
        Parsed configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML is invalid
    """
    with open(path) as f:
        return yaml.safe_load(f)
```

**src/hozo/cli.py:**

```python
"""Command-line interface for HōzÅ."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """HōzÅ - Wake-on-demand ZFS backup orchestrator."""
    pass


@main.command()
@click.argument("job_name")
def run(job_name: str) -> None:
    """Run a backup job by name."""
    click.echo(f"Running job: {job_name}")
    click.echo("(Not implemented yet - see Ho 1)")


@main.command()
def status() -> None:
    """Show status of backup jobs."""
    click.echo("Status: Not implemented yet - see Ho 2")


if __name__ == "__main__":
    main()
```

### 3.3 Commit Package Structure

```bash
git add src/ tests/
git commit -m "feat: create package structure with placeholder modules"
```

---

## Phase 4: Write First Tests

**The discipline:** Write tests for what the code SHOULD do, even before it works.

### 4.1 Test Configuration Loader

**tests/test_config.py:**

```python
"""Tests for configuration loading."""

from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
import yaml

from hozo.config.loader import load_config


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        """Should load a valid YAML config file."""
        config_data = {
            "jobs": [
                {
                    "name": "test_backup",
                    "source": "rpool/data",
                    "target_host": "backup.local",
                    "target_dataset": "backup/data",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                }
            ]
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = load_config(config_file)

        assert result == config_data
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["name"] == "test_backup"

    def test_load_missing_file_raises(self) -> None:
        """Should raise FileNotFoundError for missing config."""
        with pytest.raises(FileNotFoundError):
            load_config(Path("/nonexistent/config.yaml"))

    def test_load_empty_file(self, tmp_path: Path) -> None:
        """Should return None for empty YAML file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        result = load_config(config_file)

        assert result is None

    def test_load_invalid_yaml_raises(self, tmp_path: Path) -> None:
        """Should raise error for invalid YAML syntax."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: content: [")

        with pytest.raises(yaml.YAMLError):
            load_config(config_file)
```

### 4.2 Test WOL Module

**tests/test_wol.py:**

```python
"""Tests for Wake-on-LAN functionality."""

from unittest.mock import patch

from hozo.core.wol import wake


class TestWake:
    """Tests for wake function."""

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_sends_magic_packet(self, mock_send: patch) -> None:
        """Should call send_magic_packet with correct MAC."""
        mac = "AA:BB:CC:DD:EE:FF"

        result = wake(mac)

        assert result is True
        mock_send.assert_called_once_with(
            mac, ip_address="255.255.255.255", port=9
        )

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_custom_broadcast(self, mock_send: patch) -> None:
        """Should use custom broadcast IP when provided."""
        mac = "AA:BB:CC:DD:EE:FF"
        broadcast = "192.168.1.255"

        wake(mac, ip_address=broadcast)

        mock_send.assert_called_once_with(
            mac, ip_address=broadcast, port=9
        )

    @patch("hozo.core.wol.send_magic_packet")
    def test_wake_custom_port(self, mock_send: patch) -> None:
        """Should use custom port when provided."""
        mac = "AA:BB:CC:DD:EE:FF"

        wake(mac, port=7)

        mock_send.assert_called_once_with(
            mac, ip_address="255.255.255.255", port=7
        )
```

### 4.3 Test Job Dataclass

**tests/test_job.py:**

```python
"""Tests for job orchestration."""

import pytest

from hozo.core.job import BackupJob, run_job


class TestBackupJob:
    """Tests for BackupJob dataclass."""

    def test_create_job_with_required_fields(self) -> None:
        """Should create job with required fields only."""
        job = BackupJob(
            name="weekly",
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
            mac_address="AA:BB:CC:DD:EE:FF",
        )

        assert job.name == "weekly"
        assert job.shutdown_after is True  # default
        assert job.timeout == 120  # default

    def test_create_job_with_all_fields(self) -> None:
        """Should create job with all fields specified."""
        job = BackupJob(
            name="nightly",
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
            mac_address="AA:BB:CC:DD:EE:FF",
            shutdown_after=False,
            timeout=300,
        )

        assert job.shutdown_after is False
        assert job.timeout == 300


class TestRunJob:
    """Tests for run_job function."""

    def test_run_job_not_implemented(self) -> None:
        """Should raise NotImplementedError until Ho 1."""
        job = BackupJob(
            name="test",
            source_dataset="rpool/data",
            target_host="backup.local",
            target_dataset="backup/data",
            mac_address="AA:BB:CC:DD:EE:FF",
        )

        with pytest.raises(NotImplementedError):
            run_job(job)
```

### 4.4 Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=hozo --cov-report=term-missing

# Run specific test file
pytest tests/test_config.py -v
```

**Expected output:**

```
tests/test_config.py::TestLoadConfig::test_load_valid_yaml PASSED
tests/test_config.py::TestLoadConfig::test_load_missing_file_raises PASSED
tests/test_config.py::TestLoadConfig::test_load_empty_file PASSED
tests/test_config.py::TestLoadConfig::test_load_invalid_yaml_raises PASSED
tests/test_wol.py::TestWake::test_wake_sends_magic_packet PASSED
tests/test_wol.py::TestWake::test_wake_custom_broadcast PASSED
tests/test_wol.py::TestWake::test_wake_custom_port PASSED
tests/test_job.py::TestBackupJob::test_create_job_with_required_fields PASSED
tests/test_job.py::TestBackupJob::test_create_job_with_all_fields PASSED
tests/test_job.py::TestRunJob::test_run_job_not_implemented PASSED

10 passed
```

### 4.5 Commit Tests

```bash
git add tests/
git commit -m "test: add initial test suite for config, wol, and job modules"
```

---

## Phase 5: Code Quality Commands

### 5.1 The Quality Pipeline

Create a simple script or just memorize these commands:

```bash
# Format code (auto-fixes)
black src/ tests/

# Sort imports (auto-fixes)
isort src/ tests/

# Check style (reports issues)
flake8 src/ tests/

# Type check (reports issues)
mypy src/

# Run tests
pytest

# ALL AT ONCE (copy this)
black src/ tests/ && isort src/ tests/ && flake8 src/ tests/ && mypy src/ && pytest
```

### 5.2 Run the Full Pipeline

```bash
# Run everything
black src/ tests/ && isort src/ tests/ && flake8 src/ tests/ && mypy src/ && pytest
```

**What each tool does:**

| Tool   | Purpose                                        | Auto-fixes?       |
| ------ | ---------------------------------------------- | ----------------- |
| black  | Code formatting (line length, quotes, spacing) | Yes               |
| isort  | Import sorting and grouping                    | Yes               |
| flake8 | Style guide enforcement (PEP 8)                | No (reports only) |
| mypy   | Static type checking                           | No (reports only) |
| pytest | Run tests                                      | No                |

### 5.3 Understanding the Output

**black:**

```
All done! ✨ 🍰 ✨
6 files reformatted, 2 files left unchanged.
```

**isort:**

```
Fixing /path/to/file.py
```

**flake8:**

```
(silence = good, no issues found)
```

**mypy:**

```
Success: no issues found in 6 source files
```

If you see errors, fix them before committing!

---

## Phase 6: Create Documentation

### 6.1 README.md

````markdown
# HōzÅ (宝蔵)

**Treasure Storehouse** - A wake-on-demand ZFS backup orchestrator.

## What It Does

HōzÅ automates off-site ZFS backups to a sleeping machine:

1. **Wake** the remote backup server (Wake-on-LAN)
2. **Wait** for SSH to become available
3. **Sync** ZFS datasets using syncoid
4. **Shutdown** the remote server

Perfect for home-lab users who want off-site backups without running a second NAS 24/7.

## Installation

```bash
pip install hozo
```
````

Or with Docker:

```bash
docker run -d --name hozo ghcr.io/yourusername/hozo
```

## Quick Start

1. Create a config file:

```yaml
# config.yaml
jobs:
  - name: weekly
    source: rpool/data
    target_host: backup.tailnet.ts.net
    target_dataset: backup/rpool-data
    mac_address: 'AA:BB:CC:DD:EE:FF'
    schedule: 'weekly Sunday 03:00'
    shutdown_after: true
```

2. Run a backup:

```bash
hozo run weekly
```

## Requirements

**On the controller (where HōzÅ runs):**

- Python 3.10+
- syncoid installed
- SSH key access to remote

**On the remote backup box:**

- ZFS installed
- SSH enabled
- Wake-on-LAN enabled in BIOS
- Tailscale (or other VPN) for remote access

## Development

```bash
# Clone and setup
git clone https://github.com/yourusername/hozo
cd hozo
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
pip install -e .

# Run quality checks
black src/ tests/ && isort src/ tests/ && flake8 src/ tests/ && mypy src/ && pytest
```

## License

MIT

````

### 6.2 Example Config

**configs/config.example.yaml:**
```yaml
# HōzÅ Configuration Example
# Copy to config.yaml and customize

# Global settings
settings:
  # Default timeout for SSH connection (seconds)
  ssh_timeout: 120
  # Default SSH user
  ssh_user: root
  # Notification settings (optional)
  notifications:
    ntfy_topic: hozo-backups  # ntfy.sh topic

# Backup jobs
jobs:
  - name: weekly_full
    description: "Weekly full backup to parents' house"

    # Source (local) ZFS dataset
    source: rpool/data

    # Target remote host (Tailscale hostname recommended)
    target_host: backup-box.tailnet.ts.net
    target_dataset: backup/home-data

    # Wake-on-LAN settings
    mac_address: "AA:BB:CC:DD:EE:FF"
    # Optional: broadcast address for WOL
    # broadcast_ip: "192.168.1.255"

    # Behavior
    recursive: true          # Include child datasets
    shutdown_after: true     # Shutdown remote after backup

    # Schedule (cron-like, implemented in Ho 3)
    schedule: "weekly Sunday 03:00"

    # Retry settings
    retries: 3
    retry_delay: 60  # seconds between retries

  - name: nightly_critical
    description: "Nightly backup of critical data"
    source: rpool/critical
    target_host: backup-box.tailnet.ts.net
    target_dataset: backup/critical
    mac_address: "AA:BB:CC:DD:EE:FF"
    recursive: false
    shutdown_after: true
    schedule: "daily 02:00"
````

### 6.3 Commit Documentation

```bash
git add README.md configs/
git commit -m "docs: add README and example configuration"
```

---

## Phase 7: Final Commit & Verify

### 7.1 Run Full Quality Check

```bash
black src/ tests/ && isort src/ tests/ && flake8 src/ tests/ && mypy src/ && pytest
```

All should pass.

### 7.2 Verify CLI Works

```bash
# Test CLI is installed
hozo --version
# Output: hozo, version 0.1.0

hozo --help
# Output: Usage: hozo [OPTIONS] COMMAND [ARGS]...

hozo run test
# Output: Running job: test
#         (Not implemented yet - see Ho 1)
```

### 7.3 Final Commit

```bash
git add -A
git commit -m "chore: complete Ho 0 - project setup and foundations"
```

### 7.4 View Git Log

```bash
git log --oneline
```

Expected:

```
abc1234 chore: complete Ho 0 - project setup and foundations
def5678 docs: add README and example configuration
ghi9012 test: add initial test suite for config, wol, and job modules
jkl3456 feat: create package structure with placeholder modules
mno7890 chore: add Python project configuration
pqr1234 chore: initialize repository with .gitignore
```

---

## Success Criteria

### ✅ Checklist

- [ ] Git repository initialized with clean .gitignore
- [ ] pyproject.toml configures black, isort, mypy, pytest
- [ ] .flake8 configures flake8
- [ ] Virtual environment created and dependencies installed
- [ ] Package structure created (src/hozo/, tests/)
- [ ] Placeholder modules pass linting
- [ ] 10 tests written and passing
- [ ] `hozo --help` works
- [ ] Full quality pipeline passes: `black && isort && flake8 && mypy && pytest`
- [ ] README.md documents the project
- [ ] Example config created
- [ ] 6+ clean git commits with good messages

---

## What You Learned

### Git

- Initialize a repository
- Write a comprehensive .gitignore
- Commit with conventional messages (feat:, fix:, docs:, test:, chore:)

### Project Setup

- pyproject.toml as single source of configuration
- Separate requirements.txt for prod vs dev
- src/ layout for packages
- Editable installs with `pip install -e .`

### Testing

- pytest basics: test classes, assertions, fixtures
- tmp_path fixture for temporary files
- Mocking with unittest.mock.patch
- Test naming conventions

### Code Quality

- black: opinionated code formatter
- isort: import sorter
- flake8: style checker
- mypy: type checker
- Running them together as a pipeline

---

## What's Next: Ho 1

**Ho 1: Core Engine** will implement the placeholder modules:

- SSH polling with paramiko
- Syncoid subprocess wrapper
- Job execution orchestration
- Real end-to-end backup workflow

You now have a solid foundation to build on.

---

**Completed:** [Date]
**Time Spent:** ~2 hours
**Tests:** 10 passing
**Commits:** 6
