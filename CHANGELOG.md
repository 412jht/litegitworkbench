# Release notes

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
- Include the comparison, merge and editor modules in Windows portable packaging; retain the non-EXE release format.
