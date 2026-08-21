# LiteGit Workbench 2.0.0 release guide

## Release targets

| Target | Package | Recommended minimum |
|---|---|---|
| Windows | `LiteGitWorkbench.pyz` and `run-portable.cmd` in a ZIP archive | Windows 10 22H2 or Windows 11; Python 3.10+ with Tkinter; Git |
| Linux x64 | Single-file ELF executable in a `tar.gz` archive | Ubuntu 22.04 or another glibc 2.35+ distribution |
| macOS Intel | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |
| macOS Apple Silicon | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |

The Windows package is architecture-neutral Python source packaging. It uses the destination machine's administrator-approved Python and Tk runtime. Git remains an external requirement on every platform so the application can use the operator's existing credentials, SSH configuration and Git version.

Linux and macOS packages must be built natively on their matching operating system and processor architecture. They include Python and Tk/Tcl.

## Windows packaging and protection boundary

The Windows release uses the Python standard library's ZipApp format with ZIP compression. It contains no EXE, DLL, PyInstaller bootloader or native Python extension. After extraction, double-click `run-portable.cmd`; the launcher uses `py -3` when available and otherwise tries `python`.

ZipApp is a lightweight packaging shell, not cryptographic source protection. A recipient can inspect the Python modules in the archive. Strong anti-reverse-engineering protection requires native code or a protected runtime, which would reintroduce executable or `.pyd` files and may be blocked by the same AppLocker or Windows Defender Application Control policy.

Never include secrets, access tokens, passwords or private keys in any package.

## Local Windows portable build

```powershell
py -3 packaging/build_windows_portable.py
```

This command uses only the Python standard library. It:

1. creates `LiteGitWorkbench.pyz`;
2. runs the archive's headless smoke test with the current Python runtime;
3. packages the archive, launcher and documentation in a ZIP file;
4. proves that the ZIP and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files;
5. writes release metadata and a SHA-256 manifest.

The ignored `.litegit-local-policy.json` continues to block the legacy native Windows builder before it can create a build directory. It does not block the non-EXE portable builder.

## Linux and macOS native builds

```sh
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build_release.py
```

The native builder creates and smoke-tests the matching ELF executable or macOS application bundle. Unsigned and unnotarised macOS builds may be blocked by Gatekeeper on first launch. A Linux single-file build inherits the minimum glibc version of its build environment.

## Automated builds and GitHub Releases

`.github/workflows/build-native-releases.yml` tests and builds four targets:

- `windows-latest` creates the non-EXE portable ZIP without installing or invoking PyInstaller;
- `ubuntu-22.04` creates the Linux x64 native archive;
- `macos-15-intel` creates the macOS Intel native archive;
- `macos-15` creates the macOS Apple Silicon native archive.

A manual workflow run keeps the packages as Actions artefacts. Pushing a `v*` tag additionally creates or updates the matching GitHub Release and attaches all platform archives, metadata and the combined SHA-256 manifest. The Release page therefore provides real platform packages alongside GitHub's automatically generated source archives.

## Release verification checklist

- The full test suite passes on every target.
- The Windows ZipApp smoke test reports version `2.0.0` and `frozen: false`.
- The Windows archive contains `LiteGitWorkbench.pyz` and `run-portable.cmd`.
- The Windows archive and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files.
- Linux and macOS frozen smoke tests report version `2.0.0` and `frozen: true`.
- Native structural verification reports the expected ELF or Mach-O header.
- The repository text and Git commit messages contain ASCII-only British English.
- No private-key marker, access token or personal absolute path appears in source or packaged bytes.
- SHA-256 values match the generated manifest.
- Signing and notarisation status are reported accurately.
