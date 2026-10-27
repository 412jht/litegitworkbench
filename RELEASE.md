# LiteGit Workbench 2.1.4 release guide

## Current validation status

Version 2.1.4 is an unpublished test candidate. Use the Python portable package for current Windows testing. On 27 October 2026, the affected user confirmed that expanding/collapsing Projects and manually selecting a project work with this package. The user has observed no Kaspersky alerts from script launches. Other Git workflows still require their normal acceptance checks.

The native 2.1.4 candidate became unresponsive during a sidebar check. Read-only inspection found suspended threads; the source of suspension remains unknown. The hang does not establish a new Kaspersky detection. Native EXE compatibility remains unresolved and has not been accepted for publication.

## Release targets

| Target | Package | Recommended minimum |
|---|---|---|
| Windows x64 | ZIP containing `LiteGitWorkbench.exe` and all adjacent runtime files and directories | Windows 10 22H2 or Windows 11; Git |
| Windows portable | `LiteGitWorkbench.pyz` and `run-portable.cmd` in a ZIP archive | Windows 10 22H2 or Windows 11; Python 3.10+ with Tkinter; Git |
| Linux x64 | Single-file ELF executable in a `tar.gz` archive | Ubuntu 22.04 or another glibc 2.35+ distribution |
| macOS Intel | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |
| macOS Apple Silicon | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |

The Windows native package includes Python and Tk/Tcl beside the compiled executable. Extract the entire ZIP into one directory and run `LiteGitWorkbench.exe`; copying the EXE alone is insufficient. The separate portable package is architecture-neutral Python source packaging and uses the destination machine's administrator-approved Python and Tk runtime. Git remains an external requirement on every platform so the application can use the operator's existing credentials, SSH configuration and Git version.

Linux and macOS packages must be built natively on their matching operating system and processor architecture. They include Python and Tk/Tcl.

## Windows packaging and protection boundary

The native Windows package uses Nuitka 4.3.1 with MSVC to compile a standalone directory application. It does not use PyInstaller, a single-file extractor or UPX. Linux and macOS continue to use PyInstaller. The Windows EXE is unsigned. Windows SmartScreen, AppLocker or Windows Defender Application Control may restrict it according to the destination machine's policy.

Version 2.1.1 isolates the frozen runtime's library search paths before launching external Git commands. Python extension modules retain their private DLL directory, while Git inherits the standard Windows DLL search path and a PATH without bundled runtime directories. Git is launched by its absolute executable path. This addresses a runtime isolation defect; it does not constitute antivirus certification or guarantee that every security product will allow the unsigned executable. Keep protection enabled during verification.

Kaspersky reported the 2.1.2 test EXE through behaviour and memory inspection, and also reported the 2.1.3 PyInstaller directory EXE at startup. Their hashes match the recorded build artefacts. These observations do not establish a false-positive diagnosis or identify a precise trigger. Version 2.1.4 changes the Windows compiler and adds local phase diagnostics; its native compatibility investigation remains open. Automated build and smoke-test success does not establish antivirus clearance.

Use `start-without-restore.cmd`, or the `--no-restore` argument, to open the interface without automatically opening the last repository or querying worktree labels. Saved project shortcuts remain available. Native Windows startup phases are written once per process under `%LOCALAPPDATA%\LiteGitWorkbench\diagnostics`; `LITEGIT_STARTUP_LOG` selects an explicit path. Phases distinguish Python entry, Tk creation, settings loading, interface construction, window mapping, repository selection and external Git preparation/launch/completion. Logs contain no repository path, command argument or credential, and are not uploaded. Missing log output cannot by itself prove that the runtime was the detection trigger.

The portable Windows release uses the Python standard library's ZipApp format with ZIP compression. It contains no EXE, DLL, PyInstaller bootloader or native Python extension. After extraction, double-click `run-portable.cmd` for daily use and automatic restoration of the last project. The launcher uses `py -3` when available and otherwise tries `python`, forwarding arguments and the application's exit status. The portable `start-without-restore.cmd` enables a local phase log and waits for manual project selection. Starting in this diagnostic mode retains saved project shortcuts and leaves the normal launcher's restoration behaviour unchanged.

ZipApp is a lightweight packaging shell. A recipient can inspect the Python modules in the archive. Neither the ZipApp nor the PyInstaller package provides cryptographic source protection.

Never include secrets, access tokens, passwords or private keys in any package.

## Local Windows portable build

```powershell
py -3 packaging/build_windows_portable.py
```

This command uses only the Python standard library. It:

1. creates `LiteGitWorkbench.pyz`;
2. runs the archive's headless smoke test with the current Python runtime;
3. packages the archive, daily and diagnostic launchers, and documentation in a ZIP file;
4. proves that the ZIP and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files;
5. writes release metadata and a SHA-256 manifest.

The ignored `.litegit-local-policy.json` can block the local native Windows builder before it creates a build directory. It does not block the portable builder or native builds in GitHub Actions.

## Native builds

```sh
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build_release.py --build-dir build/native
```

The native builder creates and smoke-tests the matching Windows EXE, Linux ELF executable or macOS application bundle. It creates a disposable Git repository, then asks the packaged application to open it, refresh it twice and read commit history. It also verifies the packaged commit editor's sequence and message helper contracts against temporary text fixtures. The check uses a temporary in-memory project session and never loads or saves the user's project settings. Linux hosts without a display verify the same repository collection commands in headless mode; Windows and macOS exercise the interface event loop. The metadata records the mode and result.

On Windows, use `py -3` in place of `python3`; a local build policy may disable native packaging. Windows output includes the complete native ZIP, metadata and its SHA-256 value. Metadata also records the bundled EXE hash and explicitly states that it requires the adjacent runtime. The packaged smoke test verifies that the runtime is inside the application directory. Unsigned and unnotarised macOS builds may be blocked by Gatekeeper on first launch. A Linux single-file build inherits the minimum glibc version of its build environment.

## Automated builds and GitHub Releases

`.github/workflows/build-native-releases.yml` tests and builds four targets:

- `windows-latest` creates a native x64 directory bundle in a ZIP and the separate portable ZIP;
- `ubuntu-22.04` creates the Linux x64 native archive;
- `macos-15-intel` creates the macOS Intel native archive;
- `macos-15` creates the macOS Apple Silicon native archive.

A manual workflow run keeps the packages as Actions artefacts. Pushing a `v*` tag additionally creates or updates the matching GitHub Release and attaches all platform archives, metadata and the combined SHA-256 manifest. New releases use `CHANGELOG.md` for their release notes. The Release page therefore provides real platform packages alongside GitHub's automatically generated source archives. Test builds remain unpublished until validation.

## Release verification checklist

- The full test suite passes on every target.
- The Windows ZipApp smoke test reports version `2.1.4` and `frozen: false`.
- The Windows portable archive contains `LiteGitWorkbench.pyz`, `run-portable.cmd` and its Python `start-without-restore.cmd` diagnostic launcher.
- The extracted Windows portable launcher forwards paths containing spaces and returns the actual application exit status on success and failure.
- The Windows portable archive and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files.
- The Windows native ZIP contains `LiteGitWorkbench.exe`, Python DLLs, the Tkinter extension, Tcl/Tk data and the remaining runtime files. No standalone EXE download is advertised. Its runtime reports `packager: Nuitka` and its metadata identifies the compiler and toolchain.
- Windows, Linux and macOS packaged smoke tests report version `2.1.4` and `frozen: true`. This field means a native packaged application, including Nuitka builds that do not set `sys.frozen`.
- Packaged repository smoke tests complete opening, two refresh cycles and commit-history loading. Windows checks report an empty inherited DLL search directory before and after the refresh.
- Packaged sequence and message editor helpers produce the exact expected text; source filenames are not used to relaunch a compiled executable.
- Startup diagnostics tolerate unavailable storage, record each phase once across workers and omit sensitive fields. Diagnostic startup retains saved project shortcuts without automatic Git work or cache writes.
- Cached project switches preserve drafts and selections without filesystem reads, settings writes or sidebar reconstruction in the switch callback.
- Only the active project view is mapped. Sidebar toggles reuse project rows and do not resize inactive project views or discard their drafts.
- Settings updates coalesce on a background worker, and closing the window flushes the final project selection. Windows cache replacement retries sharing and access errors; persistent failure uses a backup scoped to the running directory. Loading selects the newest valid snapshot. Failure of both locations preserves previous files and leaves the window available for retry.
- Native structural verification reports the expected PE, ELF or Mach-O header.
- The repository text and Git commit messages contain ASCII-only British English.
- No private-key marker, access token or personal absolute path appears in source or packaged bytes.
- SHA-256 values match the generated manifest.
- Signing and notarisation status are reported accurately.
