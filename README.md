# LiteGit Workbench

LiteGit Workbench is a compact, cross-platform Git desktop application built with Python and Tk. It provides the everyday repository, branch, history, worktree and remote operations commonly expected from an IDE without carrying a full editor runtime.

The source version requires Python 3.10 or later, Tkinter and Git. Native release packages include Python and Tk/Tcl, so the destination machine only needs Git.

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

## Native releases

- Windows x64: extract and run `LiteGitWorkbench.exe`.
- Linux x64: extract `LiteGitWorkbench`, grant execute permission and run it.
- macOS Intel: extract and open `LiteGit Workbench.app`.
- macOS Apple Silicon: extract and open `LiteGit Workbench.app`.

Unsigned builds may display SmartScreen or Gatekeeper warnings. See [RELEASE.md](RELEASE.md) for the complete build, signing and compatibility notes.

## Features

### Repositories and status

- Open an existing repository, initialise a directory or clone a remote repository.
- Remember the most recently opened repository.
- Display the current branch, HEAD, upstream, ahead/behind counts, working-tree state and worktree count.
- Refresh repository data concurrently to keep large repositories responsive.

### Branches and merging

- Display local and remote branches, HEAD, upstream tracking, divergence, author and latest commit.
- Filter, check out, create, rename and delete local branches.
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

- View status and patches.
- Stage or unstage selected files, stage or unstage everything, and discard selected changes with confirmation.
- Commit staged changes or amend the current commit.

### Comparison, worktrees, stashes and tags

- Compare two references with a three-dot diff, direct diff or statistics-only view.
- Add, open, lock, unlock, remove and prune worktrees.
- Create, inspect, apply, pop and drop stashes, including untracked files.
- Create lightweight or annotated tags, delete local tags and push tags.

### Remotes and authentication

- Add, edit and delete remotes.
- Fetch, fast-forward pull and push.
- Set an upstream on the first push and use `--force-with-lease` when explicitly selected.
- Test origin authentication before pushing.
- Discover Git AskPass helpers for encrypted SSH keys and HTTPS credentials.
- Never store or log passphrases.

### Advanced operations

- View file history, blame information, reflog and unresolved conflicts.
- Resolve a conflict with ours or theirs and mark files as resolved.
- Export or apply patches.
- Inspect and recursively initialise submodules.
- Use the built-in Git console for other Git commands without invoking a command shell.
- Require confirmation for commands that may delete, overwrite or rewrite data.
- Redact common credentials from command output.

## Testing

```powershell
py -3 -m unittest discover -s tests -v
```

The tests cover parsers, repository integration, merge behaviour, worktrees, push behaviour, commit-message rewriting, history state and the English-only repository policy.

## Scope

LiteGit Workbench covers day-to-day Git workflows and exposes the remaining Git command-line surface through its built-in console. IDE-specific editing features, graphical three-way merge editors and hosted code-review services are outside its scope.
