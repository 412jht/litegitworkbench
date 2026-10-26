# LiteGit Workbench

LiteGit Workbench is a compact, cross-platform Git desktop application built with Python and Tk. It provides the everyday repository, branch, history, worktree and remote operations commonly expected from an IDE without carrying a full editor runtime.

The source version requires Python 3.10 or later, Tkinter and Git. The Windows portable package has the same runtime requirements and intentionally contains no executable, DLL or native Python extension. Linux and macOS native packages include Python and Tk/Tcl, so those destination machines only need Git.

A checkout may disable local Windows executable packaging with an ignored `.litegit-local-policy.json` file. The supported Windows release builder never invokes PyInstaller and never creates an EXE.

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

- Windows: download the portable ZIP, extract it and double-click `run-portable.cmd`. An administrator-approved Python 3.10+ installation with Tkinter and Git must already be available.
- Linux x64: extract `LiteGitWorkbench`, grant execute permission and run it.
- macOS Intel: extract and open `LiteGit Workbench.app`.
- macOS Apple Silicon: extract and open `LiteGit Workbench.app`.

Version tags publish these packages as downloadable GitHub Release assets, not merely short-lived Actions artefacts. The Windows package contains `LiteGitWorkbench.pyz`, which is compressed application packaging rather than cryptographic obfuscation. See [RELEASE.md](RELEASE.md) for the complete build and compatibility notes.

## Features

### Repositories and status

- Open an existing repository, initialise a directory or clone a remote repository.
- Keep several repositories in one window with a collapsible project sidebar.
- Add a Git directory and switch back instantly without recreating its interface.
- Preserve each open project's tabs, filters, selections, comparisons, console and commit draft during switching.
- Remember the project list, sidebar state and most recently opened repository across restarts.
- Display the current branch, HEAD, upstream, ahead/behind counts, working-tree state and worktree count.
- Refresh repository data concurrently to keep large repositories responsive.

### Branches and merging

- Display local and remote branches, HEAD, upstream tracking, divergence, author and latest commit.
- Filter, check out, create, rename and delete local branches.
- Refresh the branch list directly from the Branches tab without reopening the repository.
- Update a selected local branch from its configured upstream without checking it out or disturbing the current branch's staged, unstaged or untracked files.
- Fast-forward where possible; merge diverged branches in a separate recovery worktree and publish the result only if the original branch has not moved.
- Create a tracking branch from a remote branch.
- Merge the selected branch into the current branch with a clear confirmation prompt.
- Abort a merge in progress.
- Rebase the current branch onto the selected branch.
- Set an upstream or delete a remote branch.

### Commit history

- Browse any reference or all references and inspect complete patches.
- Copy hashes, cherry-pick, revert and create tags.
- Distinguish local-only commits from commits reachable through remote-tracking branches by both colour and text.
- Double-click the commit-message column to edit a local-only commit.
- Edit HEAD without including unrelated staged changes.
- Edit older local history through a controlled interactive rebase with autostash protection.
- Prevent message changes for commits already published remotely.

### Local changes

- View aligned before/after text, line numbers, red removed lines and green added lines.
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

## Using the 2.1.0 workflows

In **Branches**, select a local branch with an upstream and press **Update selected**. Branches checked out in another worktree must be updated from that worktree. The current branch uses a normal merge-based pull; Git will refuse to overwrite overlapping uncommitted changes. **Refresh branches** refreshes local Git information; use **Fetch** to refresh remote-tracking references from the server.

Conflicting background updates appear in **Resolve conflicts...**, with their branch and separate worktree path. Open each file in the three-way editor, resolve every block, then press **Save and stage**. Press **Complete merge / update** to finish and publish the branch update. **Abort background update** cancels the isolated merge. The branch remains unchanged until publication; a concurrent branch movement prevents publication and preserves the recovery worktree.

The editor's left and right versions are index stages 2 and 3. During a merge these normally mean the current and incoming sides; during a rebase they mean the upstream and replayed commit. Modify/delete conflicts allow choosing the deleted side explicitly. Unresolved marker lines prevent saving. Binary, non-UTF-8, symbolic-link and submodule conflicts require external resolution; their original content is preserved. Inline previews and conflict editors are limited to 2 MiB and 20,000 lines per version to keep the interface responsive.

Use **+ Add Git directory...** in the left sidebar to add another project. Clicking a project restores its existing interface immediately. Collapsing the sidebar leaves numbered project shortcuts. Open-project drafts and interface state remain in memory for the lifetime of the window; the project paths and last active project are saved between launches. Switching projects does not check out branches, modify files or close other project sessions.

## Scope

LiteGit Workbench covers day-to-day Git workflows, text comparison and three-way conflict resolution, and exposes the remaining Git command-line surface through its built-in console. A general-purpose code editor and hosted code-review services are outside its scope.
