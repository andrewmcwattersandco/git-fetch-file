# git-fetch-file
Fetch and sync individual files or globs from other Git repositories, with commit tracking and local-change protection

```sh
# Track a file from another repo
# -b, --branch, is optional, defaults to default branch (usually main or master)
git fetch-file add https://github.com/user/awesome-lib.git utils/helper.js -b main

# Pull it into your project
# --commit is optional, defaults to no commit
git fetch-file pull --commit

# That's it! The file is now in your repo and tracked in .git-remote-files
```

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
: Specify a commit hash or tag to track. This creates a "detached" tracking that stays pinned to that exact commit/tag, similar to git's detached HEAD state.

`-b <branch>`, `--branch <branch>`
: Track a specific branch. This will always fetch the latest commit from that branch tip when pulling (especially useful with `--save` to update the manifest).

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

By default, changes are not automatically committed (following git's convention). Use `--commit` for auto-commit with a git-style default message, `-m`, `--edit`, or other commit flags to enable auto-commit after pulling files.

**OPTIONS**

`--force`
: Overwrite files with local changes. Without this flag, files with local modifications are skipped.

`--save`
: Update commit hashes in `.git-remote-files` for branch references that have moved. This is particularly useful when tracking branches (added with `-b/--branch`) to get the latest commits. Files tracking specific commit hashes or tags remain unchanged.

`--dry-run`
: Show what would be fetched without actually downloading files.

`--jobs=<n>`
: Number of parallel jobs for fetching. By default, matches the number of logical CPUs (like git's default parallelism).

`--commit`
: Auto-commit changes with a git-style default message that includes information about what was fetched (e.g., "Update README.md", "Update utils.js and config.json", "Update 5 files in src/").

`-m <message>`, `--message=<message>`
: Auto-commit changes with the specified commit message.

`--edit`
: Open editor to write commit message for auto-commit.

`--no-commit`
: Don't auto-commit changes, even if other commit flags are specified.

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

## Tracking Modes

git-fetch-file supports two distinct tracking modes that behave differently:

### Branch Tracking (`-b/--branch`)
When you track a branch with `-b` or `--branch`, git-fetch-file follows the branch tip:
- Always fetches the latest commit from that branch
- Use `pull --save` to update the manifest with new commit hashes
- Similar to how git submodules work when following a branch
- Perfect for staying current with active development

Example:
```sh
git fetch-file add repo.git src/utils.js -b main
git fetch-file pull --save  # Gets latest main and updates manifest
```

### Commit/Tag Tracking (`--commit`)
When you track a specific commit hash or tag with `--commit`, git-fetch-file pins to that exact point:
- Always fetches the same commit, even if the branch has moved
- `pull --save` has no effect (nothing to update)
- Similar to git's "detached HEAD" state
- Perfect for reproducible builds and stable dependencies

Example:
```sh
git fetch-file add repo.git src/utils.js --commit v1.2.3
git fetch-file pull --save  # No change, still at v1.2.3
```

## Performance & Workflow

git-fetch-file supports concurrent downloading for better performance:
### Concurrent Operations
git-fetch-file supports concurrent downloading for better performance:
- Default: number of parallel jobs matches the number of logical CPUs (like git's default)
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
# Track the latest commit from the main branch (updates when you pull with --save)
git fetch-file add https://github.com/user/project.git utils/logger.py -b main --comment "Logging helper"

# Track a specific commit (stays pinned to that exact commit)
git fetch-file add https://github.com/user/project.git utils/config.py --commit a1b2c3d --comment "Config at stable release"

# Track a specific tag (stays pinned to that tag)
git fetch-file add https://github.com/user/project.git utils/version.py --commit v1.2.3 --comment "Version helper"
```

#### Track a file into a specific directory
```sh
git fetch-file add https://github.com/user/project.git utils/logger.py vendor -b main --comment "Third-party logging helper"
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

# By default, the number of parallel jobs matches the number of logical CPUs on your system (like git's default)
git fetch-file pull
```

#### Auto-commit changes
```sh
# Pull and auto-commit with informative git-style default messages:
# - Single file: "Update README.md" 
# - Two files: "Update utils.js and config.json"
# - Multiple files: "Update 5 files in src/"
# - With commit info: "Update package.json to a1b2c3d"
git fetch-file pull --commit

# Pull and commit with a custom message
git fetch-file pull -m "Update vendor dependencies"

# Pull and open editor for commit message
git fetch-file pull --edit

# Pull with pre-filled commit message in editor
git fetch-file pull -m "Update dependencies" --edit

# Pull without committing (default behavior)
git fetch-file pull --no-commit
```

#### Track glob patterns
```sh
# Track all JavaScript files in src/
git fetch-file add https://github.com/user/project.git "src/*.js" --glob --comment "Source files"

# Auto-detection works too (detects glob characters)
git fetch-file add https://github.com/user/project.git "docs/**/*.md"
```

#### Track an entire repository
```sh
# Track all files from a repository (equivalent to a lightweight submodule)
git fetch-file add https://github.com/user/small-lib.git "**" --glob -b main --comment "Complete library"

# Track all files into a specific directory
git fetch-file add https://github.com/user/templates.git "**" vendor/templates --glob -b main --comment "Template collection"

# Track only specific file types from entire repo
git fetch-file add https://github.com/user/assets.git "**/*.{js,css,png}" --glob --commit v1.0.0 --comment "Static assets"

# Pull everything
git fetch-file pull --save  # Updates branch-tracked repos to latest
```

**Pro tip for entire repositories:** You may want to add tracked directories to `.gitignore` so they're not committed to your main repo, then pull them locally or in CI:

```sh
# Add to .gitignore
echo "vendor/templates/" >> .gitignore
echo "vendor/assets/" >> .gitignore

# Commit the tracking config but not the files themselves
git add .git-remote-files .gitignore
git commit -m "Track external dependencies"

# Anyone cloning your repo can then pull the dependencies
git clone your-repo.git
cd your-repo
git fetch-file pull  # Downloads all tracked files locally
```

#### Branch vs Commit Tracking
```sh
# Track a branch - gets updates when the branch moves
git fetch-file add https://github.com/user/lib.git utils.js -b develop --comment "Latest development utils"

# Track a specific commit - stays pinned forever  
git fetch-file add https://github.com/user/lib.git config.js --commit a1b2c3d --comment "Stable config"

# Track a tag - stays pinned to that release
git fetch-file add https://github.com/user/lib.git version.js --commit v2.1.0 --comment "Release version helper"

# Pull and update branch-tracked files to latest commits
git fetch-file pull --save
# utils.js gets updated if develop branch moved forward
# config.js and version.js stay at their pinned commits
```

## License
GNU General Public License v2.0
