"""Check stdio container launch arguments without needing a Docker daemon."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def launcher():
    """Load the standalone launcher, which has no MCP package dependencies."""
    path = Path(__file__).resolve().parents[1] / "containers/run_container.py"
    spec = importlib.util.spec_from_file_location("alquimia_container_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_preserves_stdio_and_host_artifacts(launcher, monkeypatch, tmp_path, capsys):
    """Rootless launch maps the host user, binds results, and never pulls an image."""
    run_root = tmp_path / "runs with spaces"
    # launcher: placeholder
    monkeypatch.setattr("sys.argv", ["launcher", "--run-root", str(run_root),
                                    "--docker-host", "unix:///test/docker.sock"])
    monkeypatch.setattr(launcher.subprocess, "check_output",
                        lambda *args, **kwargs: '["name=rootless", "name=seccomp"]')
    commands = []
    monkeypatch.setattr(launcher.os, "execvp", lambda program, args: commands.append(args))
    launcher.main()
    command, = commands
    assert command[:3] == ["docker", "--host", "unix:///test/docker.sock"]
    assert "--interactive" in command and "--tty" not in command and "-t" not in command
    assert "--network=none" in command and "--pull=never" in command
    assert "--user=0:0" in command
    assert f"type=bind,src={run_root},dst=/.alquimia/runs" in command
    assert run_root.is_dir()
    # MCP(stdio) only parse JSON
    assert capsys.readouterr().out == ""


def test_launcher_refuses_rootful_daemon(launcher, monkeypatch, tmp_path):
    """Never use container UID 0 against a rootful daemon."""
    run_root = tmp_path / "unused"
    monkeypatch.setattr("sys.argv", ["launcher", "--run-root", str(run_root)])
    monkeypatch.setattr(launcher.subprocess, "check_output",
                        lambda *args, **kwargs: '["name=seccomp"]')
    with pytest.raises(SystemExit) as caught:
        launcher.main()
    assert caught.value.code == 1
    assert not run_root.exists()


@pytest.fixture
def rootful_launcher():
    """Load the standalone rootful launcher."""
    path = Path(__file__).resolve().parents[1] / "containers/run_container_rootful.py"
    spec = importlib.util.spec_from_file_location("alquimia_rootful_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rootful_launcher_runs_as_host_user(rootful_launcher, monkeypatch, tmp_path, capsys):
    """Rootful launch uses the caller's UID:GID, drops privileges, and never pulls."""
    run_root = tmp_path / "runs with spaces"
    monkeypatch.setattr("sys.argv", ["launcher", "--run-root", str(run_root),
                                    "--docker-host", "unix:///test/docker.sock"])
    monkeypatch.setattr(rootful_launcher.subprocess, "check_output",
                        lambda *args, **kwargs: '["name=seccomp"]')
    monkeypatch.setattr(rootful_launcher.subprocess, "run", lambda *args, **kwargs: None)
    commands = []
    monkeypatch.setattr(rootful_launcher.os, "execvp",
                        lambda program, args: commands.append(args))
    rootful_launcher.main()
    command, = commands
    assert command[:3] == ["docker", "--host", "unix:///test/docker.sock"]
    assert "--interactive" in command and "--tty" not in command and "-t" not in command
    assert "--network=none" in command and "--pull=never" in command
    assert f"--user={rootful_launcher.os.getuid()}:{rootful_launcher.os.getgid()}" in command
    assert "--user=0:0" not in command
    assert "--cap-drop=ALL" in command and "--security-opt=no-new-privileges" in command
    assert f"type=bind,src={run_root},dst=/.alquimia/runs" in command
    assert run_root.is_dir()
    assert capsys.readouterr().out == ""


def test_rootful_launcher_refuses_rootless_daemon(rootful_launcher, monkeypatch, tmp_path):
    """Rootless daemons belong to run_container.py, whose UID mapping differs."""
    run_root = tmp_path / "unused"
    monkeypatch.setattr("sys.argv", ["launcher", "--run-root", str(run_root)])
    monkeypatch.setattr(rootful_launcher.subprocess, "check_output",
                        lambda *args, **kwargs: '["name=rootless", "name=seccomp"]')
    with pytest.raises(SystemExit) as caught:
        rootful_launcher.main()
    assert caught.value.code == 1
    assert not run_root.exists()


def test_rootful_launcher_reports_missing_image(rootful_launcher, monkeypatch, tmp_path, capsys):
    """A missing image fails fast with pull instructions instead of a silent pull."""
    run_root = tmp_path / "unused"
    monkeypatch.setattr("sys.argv", ["launcher", "--run-root", str(run_root)])
    monkeypatch.setattr(rootful_launcher.subprocess, "check_output",
                        lambda *args, **kwargs: '["name=seccomp"]')

    def missing(*args, **kwargs):
        raise rootful_launcher.subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(rootful_launcher.subprocess, "run", missing)
    with pytest.raises(SystemExit) as caught:
        rootful_launcher.main()
    assert caught.value.code == 1
    assert "docker pull" in capsys.readouterr().err
    assert not run_root.exists()
