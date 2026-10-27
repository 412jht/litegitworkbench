# LiteGit Workbench 2.1.7 release guide

## Current validation status

Version 2.1.7 is an unpublished EXE test candidate. It fixes summary captions and fitting values being split across lines in 2.1.6, whilst retaining the divider and Projects improvements. Complete fields flow into new rows when necessary; captions remain intact and values recover a single line when they fit the available width. The Windows package retains the native MSVC launcher and adjacent official CPython runtime used by 2.1.5. Double-clicking the EXE starts the application without requiring a separate Python installation. Release acceptance requires the candidate's automated checks and validation on the affected machine.

On 27 October 2026, the affected user confirmed that expanding/collapsing Projects and manually selecting a project work with the 2.1.4 Python package, and clarified that script launches have never raised a Kaspersky alert. The native Nuitka 2.1.4 candidate became unresponsive during a sidebar check. Read-only inspection found suspended threads; the source remains unknown. The hang does not establish a new Kaspersky detection. The user subsequently reported that the 2.1.5 EXE appeared to run without a new alert, then requested the interface refinements in 2.1.6. This is an observation on that machine, not antivirus certification.

## Release targets

| Target | Package | Recommended minimum |
|---|---|---|
| Windows x64 | ZIP containing the native `LiteGitWorkbench.exe` launcher, `LiteGitWorkbench.pyz` and the complete `runtime` directory | Windows 10 22H2 or Windows 11; Git |
| Windows portable | `LiteGitWorkbench.pyz` and `run-portable.cmd` in a ZIP archive | Windows 10 22H2 or Windows 11; Python 3.10+ with Tkinter; Git |
| Linux x64 | Single-file ELF executable in a `tar.gz` archive | Ubuntu 22.04 or another glibc 2.35+ distribution |
| macOS Intel | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |
| macOS Apple Silicon | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |

The Windows native package includes the official Python interpreter, standard library and Tk/Tcl. Extract the entire ZIP into one directory and run `LiteGitWorkbench.exe`; copying the EXE alone is insufficient. The separate portable package is architecture-neutral Python source packaging and uses the destination machine's installed Python and Tk runtime. Git remains an external requirement on every platform so the application can use the operator's existing credentials, SSH configuration and Git version.

Linux and macOS packages must be built natively on their matching operating system and processor architecture. They include Python and Tk/Tcl.

## Windows packaging and protection boundary

The native Windows entry point is a small C launcher compiled with MSVC and a static C runtime. It uses `CreateProcessW` to run `runtime\pythonw.exe -I -B LiteGitWorkbench.pyz`, forwards Unicode arguments using Windows quoting rules, waits for the application and returns its exit status. It runs with the invoking user's privileges, does not invoke a command shell and does not extract a runtime at startup. Application modules run as ordinary Python. Linux and macOS continue to use PyInstaller.

The Windows build copies the official CPython interpreter without renaming or modifying it, verifies matching file hashes, and checks Python Software Foundation Authenticode signatures for `python.exe`, `pythonw.exe`, `python3.dll` and the versioned Python DLL. The standard library, extension modules and Tcl/Tk data are included; build-machine third-party site packages and cached bytecode are excluded. `runtime-verification.json` records the runtime hashes and signature results. The native launcher itself is unsigned; verified runtime signatures do not sign the launcher or certify antivirus compatibility. Windows policy may restrict it.

Tcl/Tk 8 uses library files, while current Tcl/Tk 9 distributions can use ZIP filesystem data embedded in their DLLs. The packaged check initialises the actual Tk interpreter and verifies its library locations, loaded Tcl/Tk DLLs, Python prefixes, standard library and extension module. A missing loose `.tcl` file alone is not a failure when the delivered runtime uses embedded library data.

Version 2.1.1 isolates the frozen runtime's library search paths before launching external Git commands. Python extension modules retain their private DLL directory, while Git inherits the standard Windows DLL search path and a PATH without bundled runtime directories. Git is launched by its absolute executable path. This addresses a runtime isolation defect; it does not constitute antivirus certification or guarantee that every security product will allow the unsigned executable. Keep protection enabled during verification.

Kaspersky reported the 2.1.2 test EXE through behaviour and memory inspection, and also reported the 2.1.3 PyInstaller directory EXE at startup. Their hashes match the recorded build artefacts. These observations do not establish a false-positive diagnosis or identify a precise trigger. Version 2.1.4 changed the Windows compiler to Nuitka, and 2.1.5 tests an ordinary CPython process behind a native launcher. Automated build and smoke-test success does not establish antivirus clearance.

Use `start-without-restore.cmd`, or the `--no-restore` argument, to open the interface without automatically opening the last repository or querying worktree labels. Saved project shortcuts remain available. Native Windows startup phases are written once per process under `%LOCALAPPDATA%\LiteGitWorkbench\diagnostics`; `LITEGIT_STARTUP_LOG` selects an explicit path. Phases distinguish Python entry, Tk creation, settings loading, interface construction, window mapping, repository selection and external Git preparation/launch/completion. Logs contain no repository path, command argument or credential, and are not uploaded. Missing log output cannot by itself prove that the runtime was the detection trigger.

The native launcher also records entry, runtime readiness, the child process identifier and its exit status in a separate local phase log. It never pauses, resumes or injects into another process. External Git processes do not inherit the internal launcher marker or bundled runtime environment paths. Commit editor helpers re-enter through the application EXE.

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

The native builder creates and smoke-tests the matching Windows launcher bundle, Linux ELF executable or macOS application bundle. It creates a disposable Git repository, then asks the packaged application to open it, refresh it twice and read commit history. The Windows EXE check adds a second disposable repository, maps a transparent window, performs 20 sidebar toggles and 20 project switches, and verifies retained views and drafts through widget state without image capture. It also checks long summary values at 800 and 1500 pixels, constrained to the available desktop on smaller CI hosts, a single visible sidebar layout, and 20 divider motions that defer content resizing until release. The metadata records the actual tested widths. It checks failure exit propagation and both commit editor helper contracts with spaces and Unicode in their file paths. The check uses temporary in-memory project sessions and never loads or saves the user's project settings. Linux hosts without a display verify the repository collection commands in headless mode. The metadata records the mode and result.

On Windows, use `packaging\build-windows-native.cmd` with MSVC x64 and the Windows SDK; the wrapper finds the installed Visual Studio toolchain. A local build policy may require GitHub Actions packaging instead. Windows output includes the complete native ZIP, metadata and its SHA-256 value. Metadata records the launcher hash and the official interpreter signatures, and states that the launcher requires the adjacent archive and runtime. Unsigned and unnotarised macOS builds may be blocked by Gatekeeper on first launch. A Linux single-file build inherits the minimum glibc version of its build environment.

## Automated builds and GitHub Releases

`.github/workflows/build-native-releases.yml` tests and builds four targets:

- `windows-latest` uses Python 3.14 and MSVC to create a native x64 launcher/runtime ZIP and the separate portable ZIP;
- `ubuntu-22.04` creates the Linux x64 native archive;
- `macos-15-intel` creates the macOS Intel native archive;
- `macos-15` creates the macOS Apple Silicon native archive.

A manual workflow run keeps the packages as Actions artefacts. Pushing a `v*` tag additionally creates or updates the matching GitHub Release and attaches all platform archives, metadata and the combined SHA-256 manifest. New releases use `CHANGELOG.md` for their release notes. The Release page therefore provides real platform packages alongside GitHub's automatically generated source archives. Test builds remain unpublished until validation.

## Release verification checklist

- The full test suite passes on every target.
- The Windows ZipApp smoke test reports version `2.1.7` and `frozen: false`.
- The Windows portable archive contains `LiteGitWorkbench.pyz`, `run-portable.cmd` and its Python `start-without-restore.cmd` diagnostic launcher.
- The extracted Windows portable launcher forwards paths containing spaces and returns the actual application exit status on success and failure.
- The Windows portable archive and nested ZipApp contain no `.exe`, `.dll` or `.pyd` files.
- The Windows native ZIP contains `LiteGitWorkbench.exe`, `LiteGitWorkbench.pyz` and the official interpreter, standard library, Tkinter extension and Tcl/Tk data under `runtime`. No standalone EXE download is advertised. Its runtime reports `packager: CPython launcher` and its metadata identifies MSVC, runtime hashes and valid Python Software Foundation signatures.
- Windows, Linux and macOS packaged smoke tests report version `2.1.7` and `frozen: true`. This field identifies a native packaged application; the standard interpreter retains its normal `sys.frozen` state.
- Packaged repository smoke tests complete opening, two refresh cycles and commit-history loading. Windows checks report an empty inherited DLL search directory before and after the refresh.
- Packaged sequence and message editor helpers produce the exact expected text; source filenames are not used to relaunch a compiled executable.
- The Windows EXE passes 20 mapped sidebar toggles, 20 retained-project switches, draft preservation, Unicode argument forwarding and application failure exit propagation.
- Startup diagnostics tolerate unavailable storage, record each phase once across workers and omit sensitive fields. Diagnostic startup retains saved project shortcuts without automatic Git work or cache writes.
- Cached project switches preserve drafts and selections without filesystem reads, settings writes or sidebar reconstruction in the switch callback.
- Only the active project view is mapped. One fixed sidebar layout is visible; the other remains positioned wholly outside the window's client area. Sidebar toggles retain both layouts without remapping their controls, rewriting project rows, resizing inactive project views or discarding drafts.
- Internal divider drags keep content sizes unchanged until release, preserve child text, and release pointer capture and temporary bindings on cancellation or pane destruction. Narrow summaries retain all six captions and values inside their container, including long branch names. Captions remain on one line, and a value that fits a complete row returns to its full single-line dimensions after widening the viewport.
- Settings updates coalesce on a background worker, and closing the window flushes the final project selection. Windows cache replacement retries sharing and access errors; persistent failure uses a backup scoped to the running directory. Loading selects the newest valid snapshot. Failure of both locations preserves previous files and leaves the window available for retry.
- Native structural verification reports the expected PE, ELF or Mach-O header.
- The repository text and Git commit messages contain ASCII-only British English.
- No private-key marker, access token or personal absolute path appears in source or packaged bytes.
- SHA-256 values match the generated manifest.
- Signing and notarisation status are reported accurately.
