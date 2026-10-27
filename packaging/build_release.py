from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POLICY_PATH = PROJECT_ROOT / ".litegit-local-policy.json"
VERSION = "2.1.4"


def normalised_architecture() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine or "unknown")


def load_local_policy(path: Path = LOCAL_POLICY_PATH) -> dict[str, object]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"The local build policy could not be read: {path}") from error
    if not isinstance(policy, dict):
        raise RuntimeError(f"The local build policy must contain a JSON object: {path}")
    return policy


def local_windows_build_is_blocked(
    policy: dict[str, object],
    *,
    environment: Mapping[str, str] | None = None,
    system: str | None = None,
) -> bool:
    current_environment = os.environ if environment is None else environment
    current_system = platform.system() if system is None else system
    running_in_github_actions = current_environment.get("GITHUB_ACTIONS", "").casefold() == "true"
    return (
        current_system == "Windows"
        and not running_in_github_actions
        and policy.get("block_windows_executable_build") is True
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run(command: list[str], *, cwd: Path = PROJECT_ROOT, timeout: int | None = None) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, timeout=timeout)


def build(dist_dir: Path, work_dir: Path) -> Path:
    if platform.system() == "Windows":
        dist_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        run([
            sys.executable, "-m", "nuitka", "--standalone", "--enable-plugin=tk-inter",
            "--windows-console-mode=disable", "--msvc=latest", "--assume-yes-for-downloads",
            "--jobs=4", "--lto=no", f"--output-dir={dist_dir}",
            "--output-folder-name=LiteGitWorkbench.dist", "--output-filename=LiteGitWorkbench.exe",
            f"--report={work_dir / 'nuitka-report.xml'}",
            "--company-name=LiteGit Workbench contributors", "--product-name=LiteGit Workbench",
            "--file-description=Lightweight cross-platform Git workbench",
            f"--file-version={VERSION}.0", f"--product-version={VERSION}.0",
            str(PROJECT_ROOT / "litegit.py"),
        ])
        return dist_dir / "LiteGitWorkbench.dist"
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(work_dir),
            str(PROJECT_ROOT / "packaging" / "LiteGitWorkbench.spec"),
        ]
    )
    system = platform.system()
    if system == "Darwin":
        return dist_dir / "LiteGit Workbench.app"
    return dist_dir / "LiteGitWorkbench"


def executable_inside(artefact: Path) -> Path:
    if artefact.suffix == ".app":
        return artefact / "Contents" / "MacOS" / "LiteGitWorkbench"
    if artefact.is_dir() and platform.system() == "Windows":
        return artefact / "LiteGitWorkbench.exe"
    return artefact


def verify_artefact_structure(artefact: Path) -> dict[str, object]:
    executable = executable_inside(artefact)
    if not executable.is_file():
        raise FileNotFoundError(executable)
    size = executable.stat().st_size
    minimum_size = 64 * 1024 if platform.system() == "Windows" and artefact.is_dir() else 1024 * 1024
    if size < minimum_size:
        raise RuntimeError(f"Packaged executable is unexpectedly small: {size} bytes")
    with executable.open("rb") as stream:
        header = stream.read(4)
    system = platform.system()
    if system == "Windows" and not header.startswith(b"MZ"):
        raise RuntimeError(f"Packaged executable has no PE header: {header!r}")
    if system == "Windows" and artefact.is_dir():
        if not any(artefact.glob("python*.dll")) or not any(artefact.rglob("_tkinter.pyd")) or not any(artefact.rglob("init.tcl")) or not any(artefact.rglob("tk.tcl")):
            raise RuntimeError("The Windows directory bundle is missing its adjacent Python runtime")
    if system == "Linux" and header != b"\x7fELF":
        raise RuntimeError(f"Packaged executable has no ELF header: {header!r}")
    if system == "Darwin" and header not in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }:
        raise RuntimeError(f"Packaged executable has no Mach-O header: {header!r}")
    return {
        "status": "passed",
        "executable": executable.name,
        "size": size,
        "header": header.hex().upper(),
    }


def smoke_test(artefact: Path) -> dict[str, object]:
    executable = executable_inside(artefact)
    with tempfile.TemporaryDirectory(prefix="litegit-smoke-") as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        git = shutil.which("git")
        if git is None:
            raise RuntimeError("Git is required for the packaged repository smoke test")
        hooks = root / "empty-hooks"
        hooks.mkdir()
        run([git, "-C", str(repository), "init", "-b", "main"])
        run([
            git, "-C", str(repository), "-c", "user.name=LiteGit Build",
            "-c", "user.email=build@example.test", "-c", "commit.gpgsign=false",
            "-c", f"core.hooksPath={hooks}", "commit", "--allow-empty", "-m", "Packaged repository smoke fixture",
        ])
        output = root / "smoke.json"
        try:
            run([str(executable), "--smoke-test", str(output), "--repository", str(repository)], timeout=180)
        except subprocess.CalledProcessError as error:
            if output.is_file():
                diagnostic = json.loads(output.read_text(encoding="utf-8"))
                raise RuntimeError(f"The packaged repository smoke test failed: {diagnostic}") from error
            raise
        result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("version") != VERSION or result.get("frozen") is not True:
        raise RuntimeError(f"Unexpected smoke-test result: {result}")
    if result.get("repository_check", {}).get("status") != "passed":
        raise RuntimeError(f"The packaged repository smoke test did not pass: {result}")
    if platform.system() == "Windows" and artefact.is_dir():
        if result["repository_check"]["runtime"].get("bundle_in_application_directory") is not True:
            raise RuntimeError("The directory bundle did not use its adjacent runtime")
        if result["repository_check"]["runtime"].get("packager") != "Nuitka":
            raise RuntimeError("The Windows package did not use the configured standalone compiler")
    return result


def smoke_test_reword_helper(artefact: Path) -> dict[str, str]:
    executable = executable_inside(artefact)
    with tempfile.TemporaryDirectory(prefix="litegit-helper-smoke-") as directory:
        root = Path(directory)
        sequence, message = root / "sequence.txt", root / "message.txt"
        sequence.write_text("pick abc123 Original\n", encoding="ascii")
        message.write_text("Original\n", encoding="ascii")
        environment = dict(os.environ, LITEGIT_REWORD_TARGET="abc123", LITEGIT_REWORD_MESSAGE="Updated helper smoke message")
        for mode, path in (("sequence", sequence), ("message", message)):
            subprocess.run([str(executable), "--reword-helper", mode, str(path)], env=environment, check=True, timeout=60)
        if sequence.read_text(encoding="utf-8") != "reword abc123 Original\n" or message.read_text(encoding="utf-8") != "Updated helper smoke message\n":
            raise RuntimeError("The packaged commit editor helper did not preserve its command-line contract")
    return {"status": "passed"}


def package(artefact: Path, release_dir: Path) -> Path:
    system = platform.system().lower()
    architecture = normalised_architecture()
    base_name = f"LiteGitWorkbench-{VERSION}-{system}-{architecture}"
    release_dir.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        archive = release_dir / f"{base_name}.zip"
        if artefact.is_dir() and archive.with_suffix(".exe").exists():
            raise RuntimeError("A standalone EXE remains in the output directory. Use a clean output directory for the directory bundle.")
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            if artefact.is_dir():
                for path in sorted(artefact.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(artefact).as_posix())
            else:
                bundle.write(artefact, "LiteGitWorkbench.exe")
            bundle.write(PROJECT_ROOT / "README.md", "README.md")
            bundle.write(PROJECT_ROOT / "RELEASE.md", "RELEASE.md")
            bundle.write(PROJECT_ROOT / "CHANGELOG.md", "CHANGELOG.md")
            if artefact.is_dir():
                bundle.write(PROJECT_ROOT / "packaging" / "start-without-restore.cmd", "start-without-restore.cmd")
        if artefact.is_file():
            shutil.copy2(artefact, archive.with_suffix(".exe"))
        return archive
    archive = release_dir / f"{base_name}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=9) as bundle:
        bundle.add(artefact, arcname=artefact.name, recursive=True)
        bundle.add(PROJECT_ROOT / "README.md", arcname="README.md")
        bundle.add(PROJECT_ROOT / "RELEASE.md", arcname="RELEASE.md")
        bundle.add(PROJECT_ROOT / "CHANGELOG.md", arcname="CHANGELOG.md")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a native LiteGit Workbench release")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "release")
    parser.add_argument("--build-dir", type=Path, default=PROJECT_ROOT / "build")
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="Skip execution only when the local operating-system policy blocks new binaries",
    )
    parser.add_argument(
        "--smoke-test-status",
        choices=("blocked_by_applocker_wdac", "not_run_by_request"),
        help="Required truthful status when --skip-smoke-test is used",
    )
    arguments = parser.parse_args()
    if arguments.skip_smoke_test and not arguments.smoke_test_status:
        parser.error("--smoke-test-status is required with --skip-smoke-test")
    if arguments.smoke_test_status and not arguments.skip_smoke_test:
        parser.error("--smoke-test-status is only valid with --skip-smoke-test")
    local_policy = load_local_policy()
    if local_windows_build_is_blocked(local_policy):
        reason = str(
            local_policy.get("reason")
            or "Local Windows executable builds are disabled by .litegit-local-policy.json."
        )
        parser.error(reason)
    build_dir = arguments.build_dir.resolve()
    dist_dir = build_dir / "dist"
    work_dir = build_dir / "work"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    artefact = build(dist_dir, work_dir)
    if not artefact.exists():
        raise FileNotFoundError(artefact)
    structure = verify_artefact_structure(artefact)
    if arguments.skip_smoke_test:
        smoke: dict[str, object] = {"status": arguments.smoke_test_status}
    else:
        smoke = {"status": "passed", "result": smoke_test(artefact), "reword_helper": smoke_test_reword_helper(artefact)}
    archive = package(artefact, arguments.output_dir.resolve())
    checksum = sha256(archive)
    checksums = {archive.name: checksum}
    windows_directory_bundle = platform.system() == "Windows" and artefact.is_dir()
    metadata = {
        "app": "LiteGit Workbench",
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "architecture": normalised_architecture(),
        "python": platform.python_version(),
        "packager": "Nuitka standalone directory" if windows_directory_bundle else "PyInstaller single-file bootloader",
        "archive": archive.name,
        "sha256": checksum,
        "structural_verification": structure,
        "runtime_smoke_test": smoke,
    }
    if windows_directory_bundle:
        executable = executable_inside(artefact)
        metadata["executable"] = {
            "file": executable.relative_to(artefact).as_posix(),
            "sha256": sha256(executable),
            "runtime_directory": ".",
            "standalone": False,
            "code_signing": "unsigned",
        }
        metadata["compiler"] = {"name": "Nuitka", "version": "4.3.1", "toolchain": "MSVC"}
    elif platform.system() == "Windows":
        executable = archive.with_suffix(".exe")
        executable_checksum = sha256(executable)
        checksums[executable.name] = executable_checksum
        metadata["standalone_executable"] = {
            "file": executable.name,
            "sha256": executable_checksum,
            "code_signing": "unsigned",
        }
    metadata_path = archive.with_suffix(archive.suffix + ".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    checksum_path = arguments.output_dir.resolve() / "SHA256SUMS.txt"
    replaced_names = set(checksums)
    if windows_directory_bundle:
        replaced_names.add(archive.with_suffix(".exe").name)
    existing = []
    if checksum_path.exists():
        existing = [
            line
            for line in checksum_path.read_text(encoding="utf-8").splitlines()
            if not any(line.endswith(f"  {filename}") for filename in replaced_names)
        ]
    existing.extend(f"{value}  {filename}" for filename, value in checksums.items())
    checksum_path.write_text("\n".join(sorted(existing)) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
