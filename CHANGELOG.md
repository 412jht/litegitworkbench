# Release notes

## 2.1.7

- Keep repository summary captions intact, including Worktrees with a short count. Remove the half-width caption constraint and fixed field-width cap introduced in 2.1.6.
- Move complete caption/value pairs between retained rows as the viewport changes. Wrap a value only when its field exceeds a whole row, and restore a single line after growing the window or shortening the value.
- Check single-line captions, narrow-to-wide recovery and fitting values through widget geometry and the actual Windows EXE. Keep the established launcher/runtime approach and retain this unpublished test candidate for user acceptance.

## 2.1.6

- Preview internal divider positions whilst dragging, then resize content once on release. Cover History, Local changes, before/after comparisons and three-pane conflict editors; cancel safely on Escape, hiding or closing the pane.
- Retain separate fixed expanded and collapsed Projects layouts. Swap them without reshaping controls, rebuilding rows or resizing inactive project views; preserve project selection, keyboard focus and drafts.
- Reflow the Branch / HEAD summary into multiple rows at narrower widths and wrap long values, keeping all six fields inside the available area.
- Verify mapped geometry, pointer capture, cancellation and retained sidebar state without image capture. Retain the 2.1.5 Windows launcher/runtime approach and keep this candidate unpublished pending user acceptance.

## 2.1.5

- Supply a native Windows EXE launcher with an adjacent, unmodified official CPython runtime and standard application archive. Double-click the EXE without installing Python separately.
- Compile the launcher with MSVC, forward Unicode arguments directly to the interpreter without a command shell, and return the application's exit status. Record local launcher and Python startup phases.
- Verify official interpreter hashes and Python Software Foundation signatures, include the standard library and Tcl/Tk, and omit build-machine third-party packages and cached bytecode.
- Retain ordinary CPython DLL handling for the application, keep bundled paths and internal launch context out of external Git environments, and route commit editor helpers through the EXE.
- Exercise the real packaged EXE with two repositories, repeated refreshes, 20 mapped sidebar toggles, 20 project switches, retained drafts, Unicode helper paths and failure status propagation. Keep this candidate unpublished pending validation on the affected machine.

## 2.1.4

- Compile native Windows directory packages with pinned Nuitka 4.3.1 and MSVC after Kaspersky also blocked the 2.1.3 PyInstaller directory build. Preserve bundled Python/Tk and external Git DLL isolation. This changes the build pipeline and does not certify antivirus clearance.
- Record local startup phases without repository paths, arguments or credentials. Distinguish Python entry, interface construction and external Git initialisation; tolerate unavailable diagnostic storage.
- Add `--no-restore` and a diagnostic launcher that retain project shortcuts without automatically reopening a repository, querying worktree labels or saving project settings.
- Include daily and diagnostic Python launchers in the portable Windows ZIP. Preserve the actual application exit status in both interpreter branches and verify quoted argument forwarding against the built package.
- Record user acceptance of Python sidebar toggles and manual project selection. Recommend the Python package for current Windows testing; native EXE compatibility remains unresolved after an unresponsive sidebar session.
- Recognise compiled application runtimes explicitly and preserve packaged commit editor reinvocation. Smoke-test both sequence and message helpers in native builds.
- Remove bundled Tcl/Tk and Python-home paths from external Git environments while retaining user-configured paths outside the application.

## 2.1.3

- Package Windows native builds as a directory bundle with `LiteGitWorkbench.exe` and an adjacent `_internal` runtime. Extract the complete ZIP; the EXE requires these files. Remove single-file runtime extraction and retain DLL search isolation for external Git commands. Antivirus classification still requires validation with protection enabled.
- Map only the active repository view and retain inactive views without participating in sidebar resize layouts. Position the active view directly beside the sidebar, removing cascading host-frame layouts. Reuse existing project rows when expanding or collapsing Projects; preserve project drafts, tabs and selections.
- Retry Windows cache replacement errors after closing the temporary snapshot. If the running-directory cache remains unavailable, save a separate backup in user settings for that running directory. Restore the newest valid snapshot and preserve previous files if both locations fail.
- Verify directory bundle structure, adjacent runtime use, real Windows file sharing contention and mapped sidebar layouts in regression checks.

## 2.1.2

- Switch between retained repository views by raising the existing frame. Keep layouts, tabs, filters, selections, scroll positions and commit drafts in place; initialise the shared theme once per window.
- Update only the active project selection during cached switches. Avoid filesystem path resolution and sidebar reconstruction on this path.
- Coalesce project settings updates and save snapshots on one background worker. Flush the final selection on close, retain the previous cache after a failed atomic write, and report save errors through the interface.
- Calculate aligned file differences on the repository worker and insert coloured preview text in batches. Cancel unfinished batches when another file is selected and avoid redundant synchronised scrolling.

## 2.1.1

- Isolate frozen Python DLL and shared-library search paths from external Git processes. Retain a private DLL directory for Python extensions while restoring the standard inherited Windows search path once, safely across concurrent refresh workers.
- Resolve Git from PATH to an absolute executable and use the same runtime preparation for text commands, binary conflict contents and external folder launchers.
- Preserve configured SSH, AskPass and commit-editor settings when cleaning bundled runtime paths.
- Exercise repository opening, repeated refreshes and commit history in packaged smoke tests without reading or saving the user's project list. Verify the inherited Windows DLL search directory before and after the operation.
- Address a runtime isolation defect identified during investigation of a Kaspersky behaviour alert. Vendor-specific antivirus classification still requires validation with protection enabled.

## 2.1.0

- Identify main and linked worktrees correctly in Projects; show full names and paths on hover in expanded and collapsed sidebar modes.
- Cache project paths, the active project and worktree naming metadata in the running directory. Add project removal without deleting Git directories or losing current-window drafts.
- Arrange branch actions in groups across the page and place Local changes comparison controls at the top of the right pane.
- Add row-aware branch and commit context menus, including upstream updates and published-message protection.
- Expand untracked directories into individual files, provide directory summaries and report preview read failures inline.
- Update a selected local branch from its upstream without switching away from the current branch. Preserve staged, unstaged and untracked work; merge diverged branches in an isolated, recoverable worktree.
- Resolve merge conflicts with read-only left/right panes and an editable result. Choose individual conflict blocks, accept both sides, edit manually, save and stage, then continue the Git operation.
- Refresh branches directly from the Branches tab and retain the selected branch.
- Compare local file versions side by side with aligned line numbers and coloured changes. Choose all, staged or unstaged changes.
- Manage multiple Git directories in one window using a collapsible project sidebar. Retain each project's interface and draft while switching, and remember project paths across restarts.
- Ignore outdated preview responses and keep each project's asynchronous results in its own session.
- Include the comparison, merge and editor modules in Windows portable packaging.
- Publish a standalone Windows x64 EXE and a native ZIP alongside the portable package, with bundled Python and Tk/Tcl, smoke-test metadata and SHA-256 values for both downloads.
