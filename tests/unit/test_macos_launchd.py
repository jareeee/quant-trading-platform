import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
DEPLOY = ROOT / "deploy" / "macos"
TEMPLATE = DEPLOY / "com.quant-platform.core.plist"
RENDERER = DEPLOY / "render_plist.py"


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def _isolated_install_tree(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "project with spaces"
    shutil.copytree(DEPLOY, root / "deploy" / "macos")
    home = tmp_path / "home with spaces"
    bin_dir = tmp_path / "fake bin"
    log = tmp_path / "commands.log"
    state = tmp_path / "launchd.state"
    _write_executable(bin_dir / "uname", "#!/bin/sh\nprintf '%s\\n' Darwin\n")
    _write_executable(bin_dir / "id", "#!/bin/sh\nprintf '%s\\n' 501\n")
    _write_executable(
        bin_dir / "mv",
        '#!/bin/sh\n[ "${FAIL_MV:-0}" = 1 ] && exit 1\nexec /bin/mv "$@"\n',
    )
    _write_executable(
        bin_dir / "plutil",
        f'#!/bin/sh\n"{sys.executable}" -c '
        "'import plistlib,sys; plistlib.load(open(sys.argv[-1], \"rb\"))' \"$@\"\n",
    )
    _write_executable(
        bin_dir / "launchctl",
        "#!/bin/sh\n"
        'printf "launchctl:%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'case "$1" in\n'
        '  print) test -f "$LAUNCHD_STATE" ;;\n'
        '  bootout) rm -f "$LAUNCHD_STATE" ;;\n'
        '  bootstrap) touch "$LAUNCHD_STATE" ;;\n'
        "esac\n",
    )
    _write_executable(
        root / ".venv" / "bin" / "trading-core",
        "#!/bin/sh\n"
        'printf "trading-core:%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'test "$PWD" = "$EXPECTED_ROOT" || exit 2\n'
        'test "${CORE_CHECK_FAIL:-0}" != 1\n',
    )
    (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "COMMAND_LOG": str(log),
            "LAUNCHD_STATE": str(state),
            "EXPECTED_ROOT": str(root),
        }
    )
    return root, environment, log


def test_launchd_template_has_safe_restart_and_runtime_contract() -> None:
    with TEMPLATE.open("rb") as stream:
        plist = plistlib.load(stream)

    assert plist["Label"] == "com.quant-platform.core"
    assert plist["ProgramArguments"] == [
        "__EXECUTABLE__",
        "run",
        "--lock-file",
        "__LOCK_PATH__",
    ]
    assert plist["WorkingDirectory"] == "__PROJECT_ROOT__"
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] == 30
    assert plist["StandardOutPath"] == "__LOG_DIR__/core.stdout.log"
    assert plist["StandardErrorPath"] == "__LOG_DIR__/core.stderr.log"
    assert plist["EnvironmentVariables"] == {"HOME": "__HOME__"}
    text = TEMPLATE.read_text()
    assert "password" not in text.lower()
    assert "secret" not in text.lower()
    assert "/Users/fajar" not in text


def test_renderer_preserves_absolute_paths_with_xml_characters(tmp_path: Path) -> None:
    project = tmp_path / 'project & <core> "quoted"'
    home = tmp_path / "home & operator"
    executable = project / ".venv" / "bin" / "trading-core"
    log_dir = home / "Library" / "Logs" / "quant-platform"
    lock_path = home / "Library" / "Application Support" / "quant-platform" / "core.lock"
    output = tmp_path / "rendered.plist"

    result = subprocess.run(
        [
            sys.executable,
            str(RENDERER),
            str(TEMPLATE),
            str(output),
            "--project-root",
            str(project),
            "--home",
            str(home),
            "--executable",
            str(executable),
            "--log-dir",
            str(log_dir),
            "--lock-path",
            str(lock_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered_text = output.read_text()
    assert "&amp;" in rendered_text
    assert "&lt;" in rendered_text
    assert "&quot;" in rendered_text
    with output.open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["WorkingDirectory"] == str(project)
    assert plist["ProgramArguments"][0] == str(executable)
    assert plist["ProgramArguments"][3] == str(lock_path)
    assert plist["EnvironmentVariables"]["HOME"] == str(home)
    assert plist["StandardOutPath"] == str(log_dir / "core.stdout.log")


def test_renderer_rejects_relative_paths_and_unknown_placeholders(tmp_path: Path) -> None:
    template = tmp_path / "template.plist"
    template.write_text(
        TEMPLATE.read_text().replace("</dict>", "<string>__UNKNOWN__</string></dict>")
    )
    output = tmp_path / "rendered.plist"
    command = [
        sys.executable,
        str(RENDERER),
        str(template),
        str(output),
        "--project-root",
        "relative/project",
        "--home",
        str(tmp_path),
        "--executable",
        str(tmp_path / "trading-core"),
        "--log-dir",
        str(tmp_path / "logs"),
        "--lock-path",
        str(tmp_path / "core.lock"),
    ]

    unknown = subprocess.run(command, capture_output=True, text=True, check=False)
    template.write_text(TEMPLATE.read_text())
    relative = subprocess.run(command, capture_output=True, text=True, check=False)

    assert unknown.returncode == 1
    assert "unknown" in unknown.stderr
    assert relative.returncode == 1
    assert "absolute path" in relative.stderr
    assert not output.exists()


def test_installer_rejects_macos_tcc_protected_project_path_before_preflight(
    tmp_path: Path,
) -> None:
    root, environment, log = _isolated_install_tree(tmp_path)
    protected_root = Path(environment["HOME"]) / "Documents" / "quant-platform"
    protected_root.parent.mkdir(parents=True)
    shutil.move(root, protected_root)
    environment["EXPECTED_ROOT"] = str(protected_root)

    result = subprocess.run(
        [str(protected_root / "deploy" / "macos" / "install.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "macOS-protected directory" in result.stderr
    assert not log.exists()
    assert not (Path(environment["HOME"]) / "Library").exists()


def test_installer_check_failure_never_calls_launchctl_or_mutates_home(tmp_path: Path) -> None:
    root, environment, log = _isolated_install_tree(tmp_path)
    environment["CORE_CHECK_FAIL"] = "1"

    result = subprocess.run(
        [str(root / "deploy" / "macos" / "install.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert log.read_text().splitlines() == ["trading-core:check"]
    assert not (Path(environment["HOME"]) / "Library").exists()


def test_installer_is_idempotent_and_renders_space_safe_absolute_paths(tmp_path: Path) -> None:
    root, environment, log = _isolated_install_tree(tmp_path)
    installer = root / "deploy" / "macos" / "install.sh"

    first = subprocess.run(
        [str(installer)], env=environment, capture_output=True, text=True, check=False
    )
    second = subprocess.run(
        [str(installer)], env=environment, capture_output=True, text=True, check=False
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    target = Path(environment["HOME"]) / "Library" / "LaunchAgents" / TEMPLATE.name
    with target.open("rb") as stream:
        plist = plistlib.load(stream)
    assert plist["WorkingDirectory"] == str(root)
    assert plist["ProgramArguments"][0] == str(root / ".venv" / "bin" / "trading-core")
    assert plist["ProgramArguments"][3].startswith(str(Path(environment["HOME"])))
    assert target.stat().st_mode & 0o777 == 0o600
    lines = log.read_text().splitlines()
    assert lines.count("trading-core:check") == 2
    assert lines.count("launchctl:bootstrap gui/501 " + str(target)) == 2
    assert lines.count("launchctl:kill SIGTERM gui/501/com.quant-platform.core") == 1
    assert lines.count("launchctl:bootout gui/501/com.quant-platform.core") == 1
    assert lines.count("launchctl:print gui/501/com.quant-platform.core") >= 4


def test_installer_restores_existing_service_when_atomic_replace_fails(tmp_path: Path) -> None:
    root, environment, _ = _isolated_install_tree(tmp_path)
    installer = root / "deploy" / "macos" / "install.sh"
    assert subprocess.run([str(installer)], env=environment, check=False).returncode == 0
    target = Path(environment["HOME"]) / "Library" / "LaunchAgents" / TEMPLATE.name
    original = target.read_bytes()
    environment["FAIL_MV"] = "1"

    failed = subprocess.run(
        [str(installer)], env=environment, capture_output=True, text=True, check=False
    )

    assert failed.returncode != 0
    assert target.read_bytes() == original
    assert Path(environment["LAUNCHD_STATE"]).is_file()


def test_uninstaller_is_idempotent_and_preserves_user_data(tmp_path: Path) -> None:
    root, environment, log = _isolated_install_tree(tmp_path)
    installer = root / "deploy" / "macos" / "install.sh"
    uninstaller = root / "deploy" / "macos" / "uninstall.sh"
    installed = subprocess.run([str(installer)], env=environment, check=False)
    assert installed.returncode == 0
    home = Path(environment["HOME"])
    preserved = [
        home / "Library" / "Logs" / "quant-platform" / "core.stdout.log",
        home / "Library" / "Application Support" / "quant-platform" / "core.lock",
        root / ".env",
        root / "data" / "trading.db",
    ]
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("preserve")

    first = subprocess.run(
        [str(uninstaller)], env=environment, capture_output=True, text=True, check=False
    )
    second = subprocess.run(
        [str(uninstaller)], env=environment, capture_output=True, text=True, check=False
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    target = home / "Library" / "LaunchAgents" / TEMPLATE.name
    assert not target.exists()
    assert all(path.read_text() == "preserve" for path in preserved)
    lines = log.read_text().splitlines()
    assert lines.count("launchctl:kill SIGTERM gui/501/com.quant-platform.core") == 1
    assert lines.count("launchctl:bootout gui/501/com.quant-platform.core") == 1
    assert lines.index("launchctl:kill SIGTERM gui/501/com.quant-platform.core") < lines.index(
        "launchctl:bootout gui/501/com.quant-platform.core"
    )
    assert all("system/" not in line for line in lines)
