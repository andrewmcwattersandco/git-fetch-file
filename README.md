# git-fetch-file
Fetch and sync individual files or globs from other Git repositories, with commit tracking and local-change protection

**git-fetch-file(1)** is a utility for importing specific files from other Git repositories into your own project while keeping a manifest (.git-remote-files) that remembers where they came from and what commit they belong to.

It’s like a mini submodule, but for just the files you want.

## Features

- Pull a single file or glob from a remote Git repo
- Track origin, commit, and comments in .git-remote-files
- Optionally overwrite local changes with `--force`
- Update tracked commits with `--save`
- **Dry-run mode** to preview changes without executing them
- **Concurrent fetching** with configurable parallelism (`--jobs`)
- **Git-style output** with clean, organized status reporting
- Simple CLI interface that feels like native git commands

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

### git fetch-file add

Track a file (or glob) from a remote Git repository.

**SYNOPSIS**
```
git fetch-file add <repo> <path> [<target_dir>] [<options>]
```

**DESCRIPTION**

Adds a file or glob pattern from a remote Git repository to the tracking manifest (`.git-remote-files`). The file will be downloaded on the next `pull` operation.

**OPTIONS**

`--commit <commit>`
: Specify a commit, branch, or tag to track. Defaults to `HEAD`.

`--glob`
: Treat `<path>` as a glob pattern. Overrides auto-detection.

`--no-glob`
: Treat `<path>` as a literal filename. Overrides auto-detection.

`--comment <text>`
: Add a descriptive comment to the manifest entry.

`--dry-run`
: Show what would be added without actually modifying the manifest.

### git fetch-file pull

Download all tracked files from their respective repositories.

**SYNOPSIS**
```
git fetch-file pull [<options>]
```

**DESCRIPTION**

Fetches all files tracked in `.git-remote-files` from their source repositories. Files are downloaded to their configured target locations and local changes are detected automatically.

**OPTIONS**

`--force`
: Overwrite files with local changes. Without this flag, files with local modifications are skipped.

`--save`
: Update commit hashes in `.git-remote-files` for branch/tag references that have moved.

`--dry-run`
: Show what would be fetched without actually downloading files.

`--jobs=<n>`
: Number of parallel jobs for fetching. Default is 4, like git's default parallelism.

### git fetch-file status

View all tracked files in a clean, git-style format.

**SYNOPSIS**
```
git fetch-file status
git fetch-file list
```

**DESCRIPTION**

Lists all files currently tracked in `.git-remote-files` with their source repositories and commit information. The output format is similar to `git remote -v`.

**OUTPUT FORMAT**
```
<path>[<indicators>]    <repository> (<commit>) [# <comment>]
```

Where:
- `<indicators>` may include `(glob)` for glob patterns and `-> <target>` for custom target directories
- `<commit>` is truncated to 7 characters for display
- `<comment>` is shown if present in the manifest

## .git-remote-files

Each tracked file is recorded in .git-remote-files (INI format). Example entry:

```ini
[file "lib/util.py"]
repo = https://github.com/example/tools.git
commit = a1b2c3d
target = vendor
comment = Common utility function
```

This file should be committed to your repository.

## Performance & Workflow

### Concurrent Operations
git-fetch-file supports concurrent downloading for better performance:
- Default: 4 parallel jobs (like git's default)
- Configurable with `--jobs=<n>` 
- Particularly effective when fetching from multiple repositories
- Thread-safe with proper error isolation

### Dry-Run Mode
Always preview changes before execution:
- `--dry-run` shows exactly what would happen
- Validates repository access
- Detects conflicts and local changes
- No side effects - safe to run anytime

### Git-Style Integration
Designed to feel like native git commands:
- Familiar argument patterns (`--force`, `--jobs`, etc.)
- Clean, organized output without visual clutter
- Error and warning messages follow git conventions
- Works seamlessly with git aliases

## Examples

### Basic Usage

#### Track a remote file
```sh
git fetch-file add https://github.com/user/project.git utils/logger.py --commit main --comment "Logging helper"
```

#### Track a file into a specific directory
```sh
git fetch-file add https://github.com/user/project.git utils/logger.py vendor --commit main --comment "Third-party logging helper"
```

#### Pull it into your repo
```sh
git fetch-file pull
```

### Advanced Features

#### Preview what would be added (dry-run)
```sh
git fetch-file add https://github.com/user/project.git "src/*.js" --glob --dry-run
```
Output:
```
Would validate repository access: https://github.com/user/project.git
Repository access confirmed
Would add glob pattern src/*.js from https://github.com/user/project.git (commit: HEAD)
```

#### Preview what would be pulled
```sh
git fetch-file pull --dry-run
```
Output:
```
Would fetch:
  src/utils.js from https://github.com/user/library.git (a1b2c3d -> [new commit])
  config/webpack.js from https://github.com/company/tools.git (HEAD -> [latest])

Would skip (local changes):
  docs/README.md from https://github.com/org/templates.git (use --force to overwrite)

Up to date:
  package.json from https://github.com/user/library.git (f4e5d6c)
```

#### Fast concurrent pulling
```sh
# Use 8 parallel jobs for faster fetching
git fetch-file pull --jobs=8

# Conservative single-threaded mode
git fetch-file pull --jobs=1
```

#### Track glob patterns
```sh
# Track all JavaScript files in src/
git fetch-file add https://github.com/user/project.git "src/*.js" --glob --comment "Source files"

# Auto-detection works too (detects glob characters)
git fetch-file add https://github.com/user/project.git "docs/**/*.md"
```

## License
GNU General Public License v2.0
