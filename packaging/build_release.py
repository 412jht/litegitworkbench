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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0.0"


def normalised_architecture() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine or "unknown")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run(command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def build(dist_dir: Path, work_dir: Path) -> Path:
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
    if system == "Windows":
        return dist_dir / "LiteGitWorkbench.exe"
    if system == "Darwin":
        return dist_dir / "LiteGit Workbench.app"
    return dist_dir / "LiteGitWorkbench"


def executable_inside(artefact: Path) -> Path:
    if artefact.suffix == ".app":
        return artefact / "Contents" / "MacOS" / "LiteGitWorkbench"
    return artefact


def verify_artefact_structure(artefact: Path) -> dict[str, object]:
    executable = executable_inside(artefact)
    if not executable.is_file():
        raise FileNotFoundError(executable)
    size = executable.stat().st_size
    if size < 1024 * 1024:
        raise RuntimeError(f"Packaged executable is unexpectedly small: {size} bytes")
    with executable.open("rb") as stream:
        header = stream.read(4)
    system = platform.system()
    if system == "Windows" and not header.startswith(b"MZ"):
        raise RuntimeError(f"Packaged executable has no PE header: {header!r}")
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
        output = Path(directory) / "smoke.json"
        run([str(executable), "--smoke-test", str(output)])
        result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("version") != VERSION or result.get("frozen") is not True:
        raise RuntimeError(f"Unexpected smoke-test result: {result}")
    return result


def package(artefact: Path, release_dir: Path) -> Path:
    system = platform.system().lower()
    architecture = normalised_architecture()
    base_name = f"LiteGitWorkbench-{VERSION}-{system}-{architecture}"
    release_dir.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Windows":
        archive = release_dir / f"{base_name}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            bundle.write(artefact, "LiteGitWorkbench.exe")
            bundle.write(PROJECT_ROOT / "README.md", "README.md")
            bundle.write(PROJECT_ROOT / "RELEASE.md", "RELEASE.md")
        return archive
    archive = release_dir / f"{base_name}.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=9) as bundle:
        bundle.add(artefact, arcname=artefact.name, recursive=True)
        bundle.add(PROJECT_ROOT / "README.md", arcname="README.md")
        bundle.add(PROJECT_ROOT / "RELEASE.md", arcname="RELEASE.md")
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
        smoke = {"status": "passed", "result": smoke_test(artefact)}
    archive = package(artefact, arguments.output_dir.resolve())
    checksum = sha256(archive)
    metadata = {
        "app": "LiteGit Workbench",
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.system(),
        "architecture": normalised_architecture(),
        "python": platform.python_version(),
        "packager": "PyInstaller single-file bootloader",
        "archive": archive.name,
        "sha256": checksum,
        "structural_verification": structure,
        "runtime_smoke_test": smoke,
    }
    metadata_path = archive.with_suffix(archive.suffix + ".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    checksum_path = arguments.output_dir.resolve() / "SHA256SUMS.txt"
    existing = []
    if checksum_path.exists():
        existing = [line for line in checksum_path.read_text(encoding="utf-8").splitlines() if archive.name not in line]
    existing.append(f"{checksum}  {archive.name}")
    checksum_path.write_text("\n".join(sorted(existing)) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
