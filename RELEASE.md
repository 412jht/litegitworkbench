# LiteGit Workbench 2.1.0 release guide

## Release targets

| Target | Package | Recommended minimum |
|---|---|---|
| Windows x64 | Standalone EXE and a ZIP containing `LiteGitWorkbench.exe` | Windows 10 22H2 or Windows 11; Git |
| Windows portable | `LiteGitWorkbench.pyz` and `run-portable.cmd` in a ZIP archive | Windows 10 22H2 or Windows 11; Python 3.10+ with Tkinter; Git |
| Linux x64 | Single-file ELF executable in a `tar.gz` archive | Ubuntu 22.04 or another glibc 2.35+ distribution |
| macOS Intel | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |
| macOS Apple Silicon | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |

The Windows native package includes Python and Tk/Tcl. Its standalone EXE and the executable inside its ZIP have identical contents. The separate portable package is architecture-neutral Python source packaging and uses the destination machine's administrator-approved Python and Tk runtime. Git remains an external requirement on every platform so the application can use the operator's existing credentials, SSH configuration and Git version.

Linux and macOS packages must be built natively on their matching operating system and processor architecture. They include Python and Tk/Tcl.

## Windows packaging and protection boundary

The native Windows release uses PyInstaller to provide a single-file, windowed EXE. It is unsigned. Windows SmartScreen, AppLocker or Windows Defender Application Control may restrict it according to the destination machine's policy.

The portable Windows release uses the Python standard library's ZipApp format with ZIP compression. It contains no EXE, DLL, PyInstaller bootloader or native Python extension. After extraction, double-click `run-portable.cmd`; the launcher uses `py -3` when available and otherwise tries `python`.

ZipApp is a lightweight packaging shell. A recipient can inspect the Python modules in the archive. Neither the ZipApp nor the PyInstaller package provides cryptographic source protection.

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

The ignored `.litegit-local-policy.json` can block the local native Windows builder before it creates a build directory. It does not block the portable builder or native builds in GitHub Actions.

## Native builds

```sh
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build_release.py --build-dir build/native
```

The native builder creates and smoke-tests the matching Windows EXE, Linux ELF executable or macOS application bundle. On Windows, use `py -3` in place of `python3`; a local build policy may disable native packaging. Windows output includes a standalone EXE, its ZIP archive, metadata and SHA-256 values for both downloads. Unsigned and unnotarised macOS builds may be blocked by Gatekeeper on first launch. A Linux single-file build inherits the minimum glibc version of its build environment.

## Automated builds and GitHub Releases

`.github/workflows/build-native-releases.yml` tests and builds four targets:

- `windows-latest` creates a standalone x64 EXE, a native ZIP and the separate portable ZIP;
- `ubuntu-22.04` creates the Linux x64 native archive;
- `macos-15-intel` creates the macOS Intel native archive;
- `macos-15` creates the macOS Apple Silicon native archive.

A manual workflow run keeps the packages as Actions artefacts. Pushing a `v*` tag additionally creates or updates the matching GitHub Release and attaches the standalone EXE, all platform archives, metadata and the combined SHA-256 manifest. New releases use `CHANGELOG.md` for their release notes. The Release page therefore provides real platform packages alongside GitHub's automatically generated source archives.

## Release verification checklist

- The full test suite passes on every target.
- The Windows ZipApp smoke test reports version `2.1.0` and `frozen: false`.
- The Windows portable archive contains `LiteGitWorkbench.pyz` and `run-portable.cmd`.
- The Windows portable archive and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files.
- The Windows native ZIP contains `LiteGitWorkbench.exe`, matching the standalone EXE byte for byte.
- Windows, Linux and macOS frozen smoke tests report version `2.1.0` and `frozen: true`.
- Native structural verification reports the expected PE, ELF or Mach-O header.
- The repository text and Git commit messages contain ASCII-only British English.
- No private-key marker, access token or personal absolute path appears in source or packaged bytes.
- SHA-256 values match the generated manifest.
- Signing and notarisation status are reported accurately.
