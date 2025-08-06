# git-fetch-file
Fetch and sync individual files or globs from other Git repositories, with commit tracking and local-change protection

**git-fetch-file(1)** is a utility for importing specific files from other Git repositories into your own project while keeping a manifest (.git-remote-files) that remembers where they came from and what commit they belong to.

It’s like a mini submodule, but for just the files you want.

## Features

- Pull a single file or glob from a remote Git repo
- Track origin, commit, and comments in .git-remote-files
- Optionally overwrite local changes with `--force`
- Update tracked commits with `--save`
- Simple CLI interface

## Installation

### Option 1: Git Alias (Recommended)

Save the script anywhere and set up a Git alias:

```sh
git config --global alias.fetch-file '!python3 /path/to/git-fetch-file.py'
```

Then run it like this:

```sh
git fetch-file <subcommand> [args...]
```

### Option 2: PATH Installation

Save the script as `git-fetch-file` somewhere on your PATH.

## Commands

### add

Track a file (or glob) from a remote Git repository.

```sh
git fetch-file add <repo> <path> [--commit <commit>] [--glob] [--comment <text>]
```

- repo: The URL or path to the remote Git repo
- path: Path (or glob if --glob is used) to the file(s) in the remote repo
- --commit: Optional commit, branch, or tag to track (default: HEAD)
- --glob: If specified, interprets path as a glob pattern
- --comment: Add a descriptive comment to the manifest

### pull

Download all tracked files.

```sh
git fetch-file pull [--force] [--save]
```

- --force: Overwrite local changes to files
- --save: Update the commit in .git-remote-files if you're tracking a branch or tag that moved

### list

View all tracked files.

```sh
git fetch-file list
```

## .git-remote-files

Each tracked file is recorded in .git-remote-files (INI format). Example entry:

```ini
[file "lib/util.py"]
repo = https://github.com/example/tools
commit = a1b2c3d
glob = false
comment = Common utility function
```

This file should be committed to your repository.

## Example

### Track a remote file
```sh
git fetch-file add https://github.com/user/project utils/logger.py --commit main --comment "Logging helper"
```

### Pull it into your repo
```sh
git fetch-file pull
```

## License
GNU General Public License v2.0
