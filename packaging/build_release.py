from __future__ import annotations

import argparse
import hashlib
import importlib.util
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
VERSION = "2.1.7"


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


def verify_windows_runtime_signatures(paths: list[Path]) -> list[dict[str, str]]:
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "$results = @(foreach ($file in (ConvertFrom-Json $env:LITEGIT_RUNTIME_FILES)) { "
        "$signature = Get-AuthenticodeSignature -LiteralPath $file; "
        "@{file=(Split-Path -Leaf $file); status=[string]$signature.Status; "
        "signer=$signature.SignerCertificate.Subject; thumbprint=$signature.SignerCertificate.Thumbprint} "
        "}); ConvertTo-Json -InputObject $results -Compress"
    )
    result = subprocess.run(
        [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        env=dict(os.environ, LITEGIT_RUNTIME_FILES=json.dumps([str(path) for path in paths])),
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=90,
    )
    signatures = json.loads(result.stdout)
    for signature in signatures:
        if signature["status"] != "Valid" or "Python Software Foundation" not in (signature["signer"] or ""):
            raise RuntimeError(f"The official Python runtime signature could not be verified: {signature['file']} ({signature['status']})")
    if len(signatures) != len(paths) or {signature["file"] for signature in signatures} != {path.name for path in paths}:
        raise RuntimeError("The Python runtime signature report is incomplete")
    return signatures


def copy_windows_runtime(source: Path, destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    signed_names = ("python.exe", "pythonw.exe", "python3.dll", f"python{sys.version_info.major}{sys.version_info.minor}.dll")
    names = [*signed_names, "LICENSE.txt", *(path.name for path in source.glob("vcruntime*.dll"))]
    files = {}
    for name in names:
        shutil.copy2(source / name, destination / name)
        original, copied = sha256(source / name), sha256(destination / name)
        if original != copied:
            raise RuntimeError(f"The bundled Python runtime was modified during copying: {name}")
        files[name] = copied
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages", "test", "tests", "ensurepip", "idle_test", "turtledemo")
    for name in ("DLLs", "Lib", "tcl"):
        shutil.copytree(source / name, destination / name, ignore=ignored)
    for pattern in ("tcl*.dll", "*tk*.dll"):
        for original in (source / "DLLs").glob(pattern):
            relative = original.relative_to(source)
            copied = destination / relative
            if sha256(original) != sha256(copied):
                raise RuntimeError(f"The bundled Tcl/Tk library changed during copying: {original.name}")
            files[relative.as_posix()] = sha256(copied)
    return {
        "implementation": "Unmodified official CPython interpreter and standard library",
        "version": platform.python_version(),
        "files": files,
        "authenticode": verify_windows_runtime_signatures([destination / name for name in signed_names]),
    }


def build_windows_launcher(dist_dir: Path, work_dir: Path) -> Path:
    artefact = dist_dir / "LiteGitWorkbench.dist"
    artefact.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    runtime = copy_windows_runtime(Path(sys.base_prefix), artefact / "runtime")
    (artefact / "runtime-verification.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    specification = importlib.util.spec_from_file_location("litegit_portable_archive", PROJECT_ROOT / "packaging" / "build_windows_portable.py")
    if specification is None or specification.loader is None:
        raise RuntimeError("The application archive builder could not be loaded")
    portable = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(portable)
    archive = portable.build_zipapp(work_dir / "application")
    shutil.copy2(archive, artefact / "LiteGitWorkbench.pyz")
    resource = work_dir / "windows_launcher.res"
    run(["rc.exe", "/nologo", "/fo", str(resource), str(PROJECT_ROOT / "packaging" / "windows_launcher.rc")])
    run([
        "cl.exe", "/nologo", "/W4", "/WX", "/O2", "/MT", "/DUNICODE", "/D_UNICODE",
        str(PROJECT_ROOT / "packaging" / "windows_launcher.c"), str(resource),
        f"/Fo{work_dir / 'windows_launcher.obj'}", f"/Fe{artefact / 'LiteGitWorkbench.exe'}",
        "user32.lib", "shell32.lib", "/link", "/SUBSYSTEM:WINDOWS", "/MANIFEST:EMBED",
        f"/MANIFESTINPUT:{PROJECT_ROOT / 'packaging' / 'windows_launcher.manifest'}",
        "/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA",
    ])
    return artefact


def build(dist_dir: Path, work_dir: Path) -> Path:
    if platform.system() == "Windows":
        return build_windows_launcher(dist_dir, work_dir)
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
    minimum_size = 16 * 1024 if platform.system() == "Windows" and artefact.is_dir() else 1024 * 1024
    if size < minimum_size:
        raise RuntimeError(f"Packaged executable is unexpectedly small: {size} bytes")
    with executable.open("rb") as stream:
        header = stream.read(4)
    system = platform.system()
    if system == "Windows" and not header.startswith(b"MZ"):
        raise RuntimeError(f"Packaged executable has no PE header: {header!r}")
    if system == "Windows" and artefact.is_dir():
        runtime = artefact / "runtime"
        required = {
            "application_archive": (artefact / "LiteGitWorkbench.pyz").is_file(),
            "official_interpreter": (runtime / "pythonw.exe").is_file(),
            "standard_library": (runtime / "Lib" / "os.py").is_file(),
            "python_dll": any(runtime.glob("python*.dll")),
            "tkinter_extension": any(runtime.rglob("_tkinter*.pyd")),
            "tcl_library": any(runtime.rglob("init.tcl")) or any(path for path in (runtime / "DLLs").glob("tcl*.dll") if "tk" not in path.stem.lower()),
            "tk_library": any(runtime.rglob("tk.tcl")) or any((runtime / "DLLs").glob("*tk*.dll")),
        }
        if not all(required.values()):
            missing = [name for name, present in required.items() if not present]
            libraries = [path.name for path in (runtime / "DLLs").glob("*.dll")]
            raise RuntimeError(f"The Windows directory bundle is missing its adjacent Python runtime components: {', '.join(missing)}; available libraries: {libraries}")
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
    with tempfile.TemporaryDirectory(prefix="litegit packaged smoke ") as directory:
        root = Path(directory)
        repository = root / "repository"
        repositories = [repository]
        if platform.system() == "Windows" and artefact.is_dir():
            repositories.append(root / "alternate repository")
        git = shutil.which("git")
        if git is None:
            raise RuntimeError("Git is required for the packaged repository smoke test")
        hooks = root / "empty-hooks"
        hooks.mkdir()
        for item in repositories:
            item.mkdir()
            run([git, "-C", str(item), "init", "-b", "main"])
            run([
                git, "-C", str(item), "-c", "user.name=LiteGit Build",
                "-c", "user.email=build@example.test", "-c", "commit.gpgsign=false",
                "-c", f"core.hooksPath={hooks}", "commit", "--allow-empty", "-m", "Packaged repository smoke fixture",
            ])
        output = root / "smoke.json"
        command = [str(executable), "--smoke-test", str(output), "--repository", str(repository)]
        if len(repositories) > 1:
            command.extend(["--alternate-repository", str(repositories[1])])
        try:
            run(command, timeout=180)
        except subprocess.CalledProcessError as error:
            if output.is_file():
                diagnostic = json.loads(output.read_text(encoding="utf-8"))
                raise RuntimeError(f"The packaged repository smoke test failed: {diagnostic}") from error
            raise
        result = json.loads(output.read_text(encoding="utf-8"))
        if platform.system() == "Windows" and artefact.is_dir():
            failed_output = root / "failed smoke result.json"
            failed = subprocess.run(
                [str(executable), "--smoke-test", str(failed_output), "--repository", str(root / "missing repository")],
                check=False, timeout=60,
            )
            if failed.returncode != 1 or json.loads(failed_output.read_text(encoding="utf-8"))["repository_check"]["status"] != "failed":
                raise RuntimeError("The native launcher did not preserve the failed application's exit status")
            result["failure_exit_status"] = failed.returncode
    if result.get("version") != VERSION or result.get("frozen") is not True:
        raise RuntimeError(f"Unexpected smoke-test result: {result}")
    if result.get("repository_check", {}).get("status") != "passed":
        raise RuntimeError(f"The packaged repository smoke test did not pass: {result}")
    if platform.system() == "Windows" and artefact.is_dir():
        layout = result["repository_check"].get("layout", {})
        if layout.get("status") != "passed" or layout.get("summary_fields_visible") != 6 or not all(layout.get(name) is True for name in (
            "resize_deferred_until_release", "pointer_grab_released", "captions_single_line", "fitting_values_single_line",
        )):
            raise RuntimeError("The packaged layout interaction check did not pass")
        if result["repository_check"]["runtime"].get("bundle_in_application_directory") is not True:
            raise RuntimeError("The directory bundle did not use its adjacent runtime")
        if result["repository_check"]["runtime"].get("packager") != "CPython launcher":
            raise RuntimeError("The Windows package did not use the standard CPython launcher")
        if not all(result["repository_check"]["runtime"].get(name) is True for name in (
            "python_prefix_in_runtime", "standard_library_in_runtime", "tkinter_extension_in_runtime",
            "tcl_dll_in_runtime", "tk_dll_in_runtime", "tcl_data_in_runtime", "tk_data_in_runtime",
        )):
            raise RuntimeError(f"The application runtime independence check failed: {result['repository_check']['runtime']}")
        if result["repository_check"].get("interactions", {}).get("status") != "passed":
            raise RuntimeError("The packaged sidebar and retained-project interaction check did not pass")
    return result


def smoke_test_reword_helper(artefact: Path) -> dict[str, str]:
    executable = executable_inside(artefact)
    with tempfile.TemporaryDirectory(prefix="litegit-helper-smoke-") as directory:
        root = Path(directory)
        sequence, message = root / "sequence \u03b1 file.txt", root / "message \u03b1 file.txt"
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
        "packager": "MSVC launcher with official CPython runtime" if windows_directory_bundle else "PyInstaller single-file bootloader",
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
            "runtime_directory": "runtime",
            "standalone": False,
            "code_signing": "unsigned",
        }
        metadata["compiler"] = {"name": "MSVC", "language": "C", "runtime": "static"}
        metadata["python_runtime"] = json.loads((artefact / "runtime-verification.json").read_text(encoding="utf-8"))
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
