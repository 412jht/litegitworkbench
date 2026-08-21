# LiteGit Workbench 2.0.0 release guide

## Native release targets

| Target | Package | Recommended minimum |
|---|---|---|
| Windows x64 | Single-file `LiteGitWorkbench.exe` in a ZIP archive | Windows 10 22H2 or Windows 11 |
| Linux x64 | Single-file ELF executable in a `tar.gz` archive | Ubuntu 22.04 or another glibc 2.35+ distribution |
| macOS Intel | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |
| macOS Apple Silicon | `LiteGit Workbench.app` in a `tar.gz` archive | macOS 11 or later |

Each target must be built natively on its matching operating system and processor architecture. Release packages include Python, Tk/Tcl and the application code. Git remains an external requirement so the application can use the operator's existing credentials, SSH configuration and Git version.

## Packaging and protection boundary

Releases use the PyInstaller single-file bootloader, Python optimisation level 2 and a compressed PYZ archive. This avoids distributing plain `.py` source files and extracts the runtime into a temporary directory when launched.

This is application packaging, not cryptographic source protection. A suitably skilled analyst may still recover Python bytecode. Secrets, access tokens, passwords and private keys must never be compiled into the application.

If stronger resistance to reverse engineering is required, assess Nuitka native compilation or a commercial protection product separately, including its licensing and maintenance implications.

## Signing and operating-system warnings

- An unsigned Windows build may display SmartScreen warnings or be blocked by an organisation's AppLocker or Windows Defender Application Control policy.
- An unsigned and unnotarised macOS build may be blocked by Gatekeeper on first launch.
- A Linux single-file build inherits the minimum glibc version of its build environment.

Public distribution should use an Authenticode certificate for Windows and Developer ID signing plus Apple notarisation for macOS. The automated workflow does not invent or embed signing credentials.

## Local build

Windows:

```powershell
py -3 -m pip install -r packaging/requirements-build.txt
py -3 packaging/build_release.py
```

macOS or Linux:

```sh
python3 -m pip install -r packaging/requirements-build.txt
python3 packaging/build_release.py
```

The build command:

1. creates the native frozen application;
2. validates its PE, ELF or Mach-O header and minimum size;
3. runs the frozen application's headless smoke test;
4. packages the application with this documentation;
5. writes release metadata and a SHA-256 manifest.

If an organisation's AppLocker or WDAC policy blocks a newly generated unsigned executable before process start, a local Windows build may record that limitation explicitly:

```powershell
py -3 packaging/build_release.py --skip-smoke-test --smoke-test-status blocked_by_applocker_wdac
```

This option still performs structural verification and records the blocked runtime test truthfully. GitHub Actions never uses this option: every native CI build must pass the real frozen smoke test.

## Automated native builds

`.github/workflows/build-native-releases.yml` builds and tests four independent targets:

- `windows-latest` for Windows x64;
- `ubuntu-22.04` for Linux x64;
- `macos-15-intel` for macOS Intel;
- `macos-15` for macOS Apple Silicon.

Push a `v*` tag or start the workflow manually to create the four downloadable Actions artefacts. Every matrix job runs the full unit and integration suite before packaging.

## Release verification checklist

- The full test suite passes on every target.
- The frozen smoke test reports version `2.0.0` and `frozen: true`.
- Structural verification reports the expected executable header.
- The archive contains the application, Python runtime and Tk/Tcl runtime.
- The repository text and Git commit messages contain ASCII-only British English.
- No private-key marker, access token or personal absolute path appears in source or packaged bytes.
- SHA-256 values match the generated manifest.
- Signing and notarisation status are reported accurately.
