# LiteGit Workbench

LiteGit Workbench is a compact, cross-platform Git desktop application built with Python and Tk. It provides the everyday repository, branch, history, worktree and remote operations commonly expected from an IDE without carrying a full editor runtime.

The source version requires Python 3.10 or later, Tkinter and Git. The Windows portable package has the same runtime requirements and intentionally contains no executable, DLL or native Python extension. Windows x64, Linux and macOS native packages include Python and Tk/Tcl, so those destination machines only need Git.

A checkout may disable local Windows executable packaging with an ignored `.litegit-local-policy.json` file. GitHub Actions builds both native and portable Windows packages. The separate portable builder uses only Python's standard library.

## Start from source

Windows:

```powershell
py -3 litegit.py
```

You may also double-click `run.cmd`.

macOS or Linux:

```sh
./run.sh
```

## Downloadable releases

- Windows x64 native: extract the complete ZIP and double-click `LiteGitWorkbench.exe`. The native launcher starts the included official Python runtime and application archive. Keep `LiteGitWorkbench.pyz` and the `runtime` directory beside it. Git must already be available; no separate Python installation is required.
- Windows portable: extract the portable ZIP and double-click `run-portable.cmd`. Python 3.10+ with Tkinter and Git must already be available. Normal startup restores the last project.
- Linux x64: extract `LiteGitWorkbench`, grant execute permission and run it.
- macOS Intel: extract and open `LiteGit Workbench.app`.
- macOS Apple Silicon: extract and open `LiteGit Workbench.app`.

Version tags publish these packages as downloadable GitHub Release assets, not merely short-lived Actions artefacts. The Windows portable package contains `LiteGitWorkbench.pyz`, which is compressed application packaging rather than cryptographic obfuscation. See [RELEASE.md](RELEASE.md) for the complete build and compatibility notes.

Version 2.2.0 promotes the user-accepted 2.1.7 test candidate to a stable release. It combines retained project views and responsive switching, background cache saves, batched file previews, divider drag previews and summary fields that keep their captions intact. The Windows native package uses the established MSVC EXE launcher and adjacent official CPython runtime.

## Features

### Repositories and status

- Open an existing repository, initialise a directory or clone a remote repository.
- Keep several repositories in one window with a collapsible project sidebar.
- Add a Git directory and switch back instantly without recreating its interface.
- Preserve each open project's tabs, filters, selections, comparisons, console and commit draft during switching.
- Keep cached project switches responsive while settings save in the background and large file previews load in batches.
- Retain inactive project views without resizing them when the sidebar changes width.
- Switch between retained, fixed expanded/collapsed sidebar layouts without reshaping controls or rewriting project rows.
- Preview internal divider positions whilst dragging and resize the contents once on release; press Escape to cancel.
- Remember the project list, sidebar state and most recently opened repository across restarts.
- Show just the project name for the main worktree and `worktree-name / main-project` for linked worktrees.
- Hover over project names or collapsed numbered shortcuts to see the complete name and absolute path.
- Remove entries from Projects without deleting repositories, files, drafts or pending Git operations.
- Display the current branch, HEAD, upstream, ahead/behind counts, working-tree state and worktree count.
- Move complete summary fields between rows as space changes. Keep captions on one line and wrap only values that exceed a whole row's available width; restore single-line values when they fit again.
- Refresh repository data concurrently to keep large repositories responsive.

### Branches and merging

- Display local and remote branches, HEAD, upstream tracking, divergence, author and latest commit.
- Filter, check out, create, rename and delete local branches.
- Refresh the branch list directly from the Branches tab without reopening the repository.
- Use the branch row's context menu to update, check out, merge, rename or delete the selected branch.
- Update a selected local branch from its configured upstream without checking it out or disturbing the current branch's staged, unstaged or untracked files.
- Fast-forward where possible; merge diverged branches in a separate recovery worktree and publish the result only if the original branch has not moved.
- Create a tracking branch from a remote branch.
- Merge the selected branch into the current branch with a clear confirmation prompt.
- Abort a merge in progress.
- Rebase the current branch onto the selected branch.
- Set an upstream or delete a remote branch.

### Commit history

- Browse any reference or all references and inspect complete patches.
- Right-click a commit to show details, copy its hash, edit an unpublished message, cherry-pick, revert or create a tag. Published message editing remains disabled.
- Copy hashes, cherry-pick, revert and create tags.
- Distinguish local-only commits from commits reachable through remote-tracking branches by both colour and text.
- Double-click the commit-message column to edit a local-only commit.
- Edit HEAD without including unrelated staged changes.
- Edit older local history through a controlled interactive rebase with autostash protection.
- Prevent message changes for commits already published remotely.

### Local changes

- View aligned before/after text, line numbers, red removed lines and green added lines.
- List individual untracked files inside folders and show directory summaries instead of trying to open directories as files.
- Compare all changes (HEAD to working tree), staged changes (HEAD to index) or unstaged changes (index to working tree), including files with both staged and unstaged edits.
- Stage or unstage selected files, stage or unstage everything, and discard selected changes with confirmation.
- Commit staged changes or amend the current commit.

### Comparison, worktrees, stashes and tags

- Compare two references with a three-dot diff, direct diff or statistics-only view.
- Add, open, lock, unlock, remove and prune worktrees.
- Create, inspect, apply, pop and drop stashes, including untracked files.
- Create lightweight or annotated tags, delete local tags and push tags.

### Remotes and authentication

- Add, edit and delete remotes.
- Fetch, merge-based pull and push. Pull conflicts open the conflict manager.
- Set an upstream on the first push and use `--force-with-lease` when explicitly selected.
- Test origin authentication before pushing.
- Discover Git AskPass helpers for encrypted SSH keys and HTTPS credentials.
- Never store or log passphrases.

### Advanced operations

- View file history, blame information, reflog and unresolved conflicts.
- Resolve text conflicts in three panes: read-only left and right versions alongside an editable result.
- Navigate conflict blocks, choose either side or both, edit the result manually, and save and stage the resolution.
- Continue a merge, rebase, cherry-pick or revert after resolving its conflicts.
- Recover or abort a pending background branch update after restarting the application.
- Resolve a conflict with ours or theirs and mark files as resolved through the advanced tools.
- Export or apply patches.
- Inspect and recursively initialise submodules.
- Use the built-in Git console for other Git commands without invoking a command shell.
- Require confirmation for commands that may delete, overwrite or rewrite data.
- Redact common credentials from command output.

## Testing

```powershell
py -3 -m unittest discover -s tests -v
```

The tests cover parsers, repository integration, isolated branch updates, dirty-work preservation, conflict recovery and editing, before/after previews, project switching, stale background responses, worktrees, push behaviour, commit-message rewriting, history state and the English-only repository policy.

## Using the 2.1 workflows

In **Branches**, select a local branch with an upstream and press **Update selected**. Branches checked out in another worktree must be updated from that worktree. The current branch uses a normal merge-based pull; Git will refuse to overwrite overlapping uncommitted changes. **Refresh branches** refreshes local Git information; use **Fetch** to refresh remote-tracking references from the server.

Conflicting background updates appear in **Resolve conflicts...**, with their branch and separate worktree path. Open each file in the three-way editor, resolve every block, then press **Save and stage**. Press **Complete merge / update** to finish and publish the branch update. **Abort background update** cancels the isolated merge. The branch remains unchanged until publication; a concurrent branch movement prevents publication and preserves the recovery worktree.

The editor's left and right versions are index stages 2 and 3. During a merge these normally mean the current and incoming sides; during a rebase they mean the upstream and replayed commit. Modify/delete conflicts allow choosing the deleted side explicitly. Unresolved marker lines prevent saving. Binary, non-UTF-8, symbolic-link and submodule conflicts require external resolution; their original content is preserved. Inline previews and conflict editors are limited to 2 MiB and 20,000 lines per version to keep the interface responsive.

Use **+ Add Git directory...** in the left sidebar to add another project. Clicking a project restores its existing interface immediately. Collapsing the sidebar leaves numbered project shortcuts. Open-project drafts and interface state remain in memory for the lifetime of the window; the project paths and last active project are saved between launches. Switching projects does not check out branches, modify files or close other project sessions.

Projects use the main worktree name alone, for example `project`, and linked worktrees use `project-task / project`. Hovering reveals the full name and path in both sidebar modes. Use **Remove from Projects** or the project row's context menu to remove an entry. The Git directory and pending operations remain available; adding the directory again restores its in-memory session while the window is open.

The project list is cached in `.litegit-projects.json` in the current running directory. The one-click launchers first change into their application directory, so source and portable launches keep the cache beside the application. Existing user settings are imported when no valid local cache exists. An empty local project list is respected after removals, and the cache is written atomically. Windows sharing and access errors are retried on the settings worker. If the local cache remains unavailable, the latest snapshot is saved under the user's application settings in a separate `project-caches` file for each running directory. The next launch uses the newer valid local or backup snapshot. If both locations fail, the previous files and current window remain available and the error is reported. Runtime project caches are excluded from Git.

For daily portable use, double-click `run-portable.cmd` to restore the last project. For startup investigation, use `start-without-restore.cmd` in either Windows ZIP, or pass `--no-restore` to the application. This keeps saved project shortcuts available but waits for an explicit selection before running Git or saving project settings. The portable diagnostic launcher enables local startup logging; native Windows launches log automatically. Logs are stored under `%LOCALAPPDATA%\LiteGitWorkbench\diagnostics` and contain timestamps, process identifiers and phase names, without repository paths, command arguments or credentials. `LITEGIT_STARTUP_LOG` can select an explicit diagnostic file. No diagnostic data is uploaded.

The Branches toolbar groups branch management, integration and tracking actions across the page. Local changes keeps its file actions on the left and the Compare controls at the top of the right pane. Branch, project and commit context menus also support Shift+F10 on a selected row; macOS supports Control-click.

## Scope

LiteGit Workbench covers day-to-day Git workflows, text comparison and three-way conflict resolution, and exposes the remaining Git command-line surface through its built-in console. A general-purpose code editor and hosted code-review services are outside its scope.
