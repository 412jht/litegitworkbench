from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import zipapp
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION = "2.0.0"
APPLICATION_ARCHIVE = "LiteGitWorkbench.pyz"
SOURCE_MODULES = ("litegit.py", "litegit_core.py", "litegit_reword.py")
PROHIBITED_NATIVE_SUFFIXES = {".dll", ".exe", ".pyd"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run(command: list[str], *, cwd: Path = PROJECT_ROOT) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def build_zipapp(build_dir: Path) -> Path:
    source_dir = build_dir / "zipapp-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    for filename in SOURCE_MODULES:
        shutil.copy2(PROJECT_ROOT / filename, source_dir / filename)
    (source_dir / "__main__.py").write_text(
        "from litegit import main\n\nraise SystemExit(main())\n",
        encoding="ascii",
        newline="\n",
    )
    target = build_dir / APPLICATION_ARCHIVE
    zipapp.create_archive(source_dir, target=target, compressed=True)
    return target


def smoke_test(application: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="litegit-portable-smoke-") as directory:
        output = Path(directory) / "smoke.json"
        run([sys.executable, str(application), "--smoke-test", str(output)])
        result = json.loads(output.read_text(encoding="utf-8"))
    if result.get("version") != VERSION or result.get("frozen") is not False:
        raise RuntimeError(f"Unexpected portable smoke-test result: {result}")
    return result


def _native_entries(archive: zipfile.ZipFile) -> list[str]:
    return [
        name
        for name in archive.namelist()
        if Path(name).suffix.casefold() in PROHIBITED_NATIVE_SUFFIXES
    ]


def verify_no_native_binaries(package: Path) -> dict[str, object]:
    with zipfile.ZipFile(package) as bundle:
        prohibited = _native_entries(bundle)
        zipapps = [name for name in bundle.namelist() if Path(name).suffix.casefold() == ".pyz"]
        for name in zipapps:
            with zipfile.ZipFile(io.BytesIO(bundle.read(name))) as application:
                prohibited.extend(f"{name}!/{entry}" for entry in _native_entries(application))
    if prohibited:
        raise RuntimeError(f"Native Windows binaries are not permitted: {', '.join(prohibited)}")
    return {
        "status": "passed",
        "prohibited_suffixes": sorted(PROHIBITED_NATIVE_SUFFIXES),
        "native_binary_count": 0,
    }


def package(application: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"LiteGitWorkbench-{VERSION}-windows-portable.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.write(application, APPLICATION_ARCHIVE)
        bundle.write(PROJECT_ROOT / "packaging" / "run-portable.cmd", "run-portable.cmd")
        bundle.write(PROJECT_ROOT / "README.md", "README.md")
        bundle.write(PROJECT_ROOT / "RELEASE.md", "RELEASE.md")
    return archive


def build_release(output_dir: Path, build_dir: Path) -> tuple[Path, dict[str, object]]:
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    application = build_zipapp(build_dir)
    smoke = smoke_test(application)
    archive = package(application, output_dir)
    structure = verify_no_native_binaries(archive)
    checksum = sha256(archive)
    metadata = {
        "app": "LiteGit Workbench",
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "platform": "Windows portable",
        "architecture": "any architecture supported by the installed Python runtime",
        "builder_platform": platform.system(),
        "python": platform.python_version(),
        "packager": "Python ZipApp with ZIP compression",
        "archive": archive.name,
        "sha256": checksum,
        "runtime_requirement": "Python 3.10 or later with Tkinter",
        "git_requirement": "Git must be installed and available on PATH",
        "source_protection": "Packaging only; not cryptographic obfuscation",
        "structural_verification": structure,
        "runtime_smoke_test": {"status": "passed", "result": smoke},
    }
    metadata_path = archive.with_suffix(archive.suffix + ".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="ascii", newline="\n")
    checksum_path = output_dir / "SHA256SUMS.txt"
    existing = []
    if checksum_path.exists():
        existing = [line for line in checksum_path.read_text(encoding="ascii").splitlines() if archive.name not in line]
    existing.append(f"{checksum}  {archive.name}")
    checksum_path.write_text("\n".join(sorted(existing)) + "\n", encoding="ascii", newline="\n")
    return archive, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the non-EXE Windows portable release")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "release")
    parser.add_argument("--build-dir", type=Path, default=PROJECT_ROOT / "build" / "windows-portable")
    arguments = parser.parse_args()
    archive, metadata = build_release(arguments.output_dir.resolve(), arguments.build_dir.resolve())
    print(json.dumps(metadata, ensure_ascii=True, indent=2))
    print(f"Created {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
