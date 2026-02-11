# git-fetch-file
Fetch and sync individual files or globs from other Git repositories, with commit tracking and local-change protection

```sh
git fetch-file add https://github.com/user/awesome-lib.git utils/helper.js
git fetch-file pull --commit
```

**git-fetch-file(1)** is a utility for importing specific files from other Git repositories into your own project while keeping a manifest (.git-remote-files) that remembers where they came from and what commit they belong to.

It’s like a mini submodule, but for just the files you want.

## Features

- Pull a single file or glob from a remote Git repo
- Track origin, commit, and comments in .git-remote-files
- **Automatic remote tracking** - remote-tracking files update to latest commit automatically
- Optionally overwrite local changes with `--force`
- **Dry-run mode** to preview changes without executing them
- **Concurrent fetching** with configurable parallelism (`--jobs`)
- **Git-style output** with clean, organized status reporting
- **Backward compatibility** with automatic migration of old manifest formats
- Simple CLI interface that feels like native git commands

## Installation

### Option 1: Go Binary (Fastest)

Download or build the Go binary for optimal performance:

```sh
# Build from source (requires Go 1.21+)
go build -o git-fetch-file

# On Windows
go build -o git-fetch-file.exe

# Set up Git alias
git config --global alias.fetch-file '!/path/to/git-fetch-file'
```

The Go implementation is ~7x faster and has no runtime dependencies.

### Option 2: Python Script (Git Alias)

Save the Python script anywhere and set up a Git alias:

```sh
git config --global alias.fetch-file '!python3 /path/to/git-fetch-file.py'
```

Then run it like this:

```sh
git fetch-file <subcommand> [args...]
```

### Option 3: PATH Installation

Save either `git-fetch-file` (Go binary) or `git-fetch-file.py` (Python script) somewhere on your PATH.

> **Note:** Both Python and Go implementations are fully compatible and can be used interchangeably. They share the same `.git-remote-files` manifest format.

## Commands

### git fetch-file add

Track a file (or glob) from a remote Git repository.

**SYNOPSIS**
```
git fetch-file add <repository> <path> [<target>] [<options>]
```

**DESCRIPTION**

Adds a file or glob pattern from a remote Git repository to the tracking manifest (`.git-remote-files`). The file will be downloaded on the next `pull` operation.

If the same file path is already being tracked from the same repository to the same target directory, an error will be shown. Use `--force` to overwrite the existing entry, or specify a different target directory to track the same file to multiple locations.

**OPTIONS**

`--detach <commit>`
: Specify a commit hash or tag to track. This creates a "detached" tracking that stays pinned to that exact commit/tag, similar to git's detached HEAD state.

`--commit <commit>`
: Alias for `--detach` (maintained for backward compatibility).

`-b <branch>`, `--branch <branch>`
: Track a specific branch. This will always fetch the latest commit from that branch tip when pulling, automatically updating the manifest with new commit hashes.

`--glob`
: Treat `<path>` as a glob pattern. Overrides auto-detection.

`--no-glob`
: Treat `<path>` as a literal filename. Overrides auto-detection.

`--is-file`
: Treat `<path>` as a file path. Overrides auto-detection.

`--is-directory`
: Treat `<path>` as a directory path. Overrides auto-detection.

`--comment <text>`
: Add a descriptive comment to the manifest entry.

`--force`
: Overwrite existing entries when there's a conflict (same file from same repository to same target directory).

`--dry-run`
: Show what would be added without actually modifying the manifest.

### git fetch-file remove

Remove a tracked file from the manifest.

**SYNOPSIS**
```
git fetch-file remove <path> [<target>]
```

**DESCRIPTION**

Removes a file or glob pattern from the tracking manifest (`.git-remote-files`). The local file is not deleted - only the tracking entry is removed.

If multiple entries exist for the same path (tracking to different targets), you must specify the target to disambiguate which entry to remove.

**OPTIONS**

`--dry-run`
: Show what would be removed without actually modifying the manifest.

### git fetch-file pull

Download all tracked files from their respective repositories.

**SYNOPSIS**
```
git fetch-file pull [<options>]
```

**DESCRIPTION**

Fetches all files tracked in `.git-remote-files` from their source repositories. Files are downloaded to their configured target locations and local changes are detected automatically.

For remote-tracking files, git-fetch-file automatically updates to the latest commit on the tracked branch and updates the manifest with the new commit hash. For commit/tag-tracked files, the exact pinned commit is fetched.

By default, changes are not automatically committed (following git's convention). Use `--commit` for auto-commit with a git-style default message, `-m`, `--edit`, or other commit flags to enable auto-commit after pulling files.

**OPTIONS**

`--force`
: Overwrite files with local changes. Without this flag, files with local modifications are skipped.

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

`-r <repository>`, `--repository=<repository>`
: Only update files coming from the given repository. Short URLs may be given (i.e., those that use `insteadOf` replacements).

`-p <path>`, `--path=<path>`
: Only update files under the given path. May be specified multiple times in which case any given `<path>` must match.

`--save`
: (Deprecated) This flag is ignored for backwards compatibility. Remote-tracking files now update automatically.

### git fetch-file status

View all tracked files in a clean, git-style format.

**SYNOPSIS**
```
git fetch-file status
git fetch-file list
```

**DESCRIPTION**

Lists all files currently tracked in `.git-remote-files` with their source repositories and commit information. The output format is similar to `git remote -v`.

When the same file path is tracked to multiple target directories or from different repositories, each entry is shown separately.

**OUTPUT FORMAT**
```
<path>[<indicators>]    <repository> (<commit>) [# <comment>]
```

Where:
- `<indicators>` may include `(glob)` for glob patterns and `-> <target>` for custom target directories
- `<commit>` is truncated to 7 characters for display
- `<comment>` is shown if present in the manifest

## .git-remote-files

Each tracked file is recorded in .git-remote-files (INI format). Example entries:

```ini
[file "lib/util.py" from "https://github.com/example/tools.git"]
commit = a1b2c3d4e5f6789abcdef0123456789abcdef01
branch = master
comment = Common utility function

[file "config.json" from "https://github.com/example/tools.git"]
commit = b2c3d4e5f6789abcdef0123456789abcdef012
branch = master
target = vendor
comment = Configuration from tools repo

[file "helper.js" from "https://github.com/another/project.git"]
commit = c3d4e5f6789abcdef0123456789abcdef0123
branch = main
comment = Helper from another project
```

This file should be committed to your repository.

**Section Naming**: All entries use the format `[file "path" from "repository_url"]` for uniqueness and clarity. Target directories and other metadata are stored as keys within each section.

This allows tracking the same filename from different repositories or to different target locations without conflicts, while keeping the manifest file human-readable.

**Note**: Starting with v1.4.0, the manifest format has been simplified to eliminate redundant repository keys. Repository information is stored only in section names. Old manifests are automatically migrated when read for full backward compatibility.

## Tracking Modes

git-fetch-file supports two distinct tracking modes that behave differently:

### Remote-tracking files (`-b/--branch`)
When you track a branch with `-b` or `--branch`, git-fetch-file follows the branch tip:
- Always fetches the latest commit from that branch automatically
- The manifest is automatically updated with new commit hashes when pulling
- Similar to how git submodules work when following a branch
- Perfect for staying current with active development

Example:
```sh
git fetch-file add repo.git src/utils.js -b main
git fetch-file pull  # Gets latest main and updates manifest automatically
```

### Commit/Tag Tracking (`--commit`)
When you track a specific commit hash or tag with `--commit`, git-fetch-file pins to that exact point:
- Always fetches the same commit, even if the branch has moved
- The manifest never changes (nothing to update)
- Similar to git's "detached HEAD" state
- Perfect for reproducible builds and stable dependencies

Example:
```sh
git fetch-file add repo.git src/utils.js --detach v1.2.3
git fetch-file pull  # No change, still at v1.2.3
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

## Handling Conflicts

git-fetch-file prevents conflicts when trying to track the same file from the same repository to the same target location. This ensures your manifest remains clean and intentional.

### Conflict Detection

A conflict occurs when you try to add a file that is already being tracked with the same:
- File path
- Repository URL  
- Target directory (or lack thereof)

```sh
# This will work fine
git fetch-file add https://github.com/user/repo.git utils.js

# This will show an error (same file, same repo, same target)
git fetch-file add https://github.com/user/repo.git utils.js
# Output: fatal: 'utils.js' already tracked from https://github.com/user/repo.git
#         hint: use --force to overwrite, or specify a different target directory
```

### Resolution Options

#### Option 1: Use --force to overwrite
```sh
git fetch-file add https://github.com/user/repo.git utils.js --force
# Overwrites the existing entry with new settings
```

#### Option 2: Use a different target directory
```sh
# Track the same file to different locations
git fetch-file add https://github.com/user/repo.git utils.js vendor
git fetch-file add https://github.com/user/repo.git utils.js src/external
# Both entries coexist peacefully
```

### Removing Tracked Files

Use the `remove` command to clean up entries you no longer need:

```sh
# Remove a basic tracking entry
git fetch-file remove utils.js

# Remove a specific target (when multiple exist)
git fetch-file remove utils.js vendor

# See what's currently tracked
git fetch-file status
```

## Examples

### Basic Usage

#### Track a remote file
```sh
# Track the latest commit from the main branch (updates automatically when you pull)
git fetch-file add https://github.com/user/project.git utils/logger.py -b main --comment "Logging helper"

# Track a specific commit (stays pinned to that exact commit)
git fetch-file add https://github.com/user/project.git utils/config.py --detach a1b2c3d --comment "Config at stable release"

# Track a specific tag (stays pinned to that tag)
git fetch-file add https://github.com/user/project.git utils/version.py --commit v1.2.3 --comment "Version helper"
```

#### Track a file into a specific directory
```sh
git fetch-file add https://github.com/user/project.git utils/logger.py vendor -b main --comment "Third-party logging helper"
```

### Target Parameter Behavior

The `<target>` parameter controls where files are placed and supports both directory placement and file renaming.

#### Single File Behavior

For single files (not globs), the target parameter is interpreted based on these rules (in order of precedence):

1. **Trailing slash (`/`)** - Always treated as directory
   ```sh
   git fetch-file add repo.git path/file.txt subdir/
   # Result: subdir/file.txt (preserves original name)
   ```

2. **`--is-directory` flag** - Forces directory treatment
   ```sh
   git fetch-file add --is-directory repo.git path/file.txt output.txt
   # Result: output.txt/file.txt (even though target has .txt extension)
   ```

3. **`--is-file` flag** - Forces file treatment (enables renaming)
   ```sh
   git fetch-file add --is-file repo.git path/README NEWNAME
   # Result: NEWNAME (renames file, even without extension)
   ```

4. **File extension heuristic** - Target with extension treated as file (enables renaming)
   ```sh
   git fetch-file add repo.git path/README README.md
   # Result: README.md (renames the file)
   
   git fetch-file add repo.git utils/config.yaml settings.json
   # Result: settings.json (renames and changes extension)
   ```

5. **No extension heuristic** - Target without extension treated as directory (default)
   ```sh
   git fetch-file add repo.git path/file.txt mydir
   # Result: mydir/file.txt (preserves original name)
   ```

#### Glob Pattern Behavior

For glob patterns, the target is always treated as a directory and the full directory structure is preserved:

```sh
git fetch-file add repo.git "src/**/*.js" vendor/
# Result: vendor/src/utils/helper.js, vendor/src/components/button.js, etc.
# (preserves full path structure under vendor/)
```

#### Renaming Examples

```sh
# Rename a file with different extension
git fetch-file add repo.git docs/README README.md

# Rename to file without extension (use --is-file)
git fetch-file add --is-file repo.git docs/CHANGELOG HISTORY

# Place file in directory (preserves name)
git fetch-file add repo.git src/config.js vendor/
git fetch-file add repo.git src/config.js vendor  # Same result (no extension = directory)

# Force directory even with extension in name
git fetch-file add --is-directory repo.git src/app.js backup.old
# Result: backup.old/app.js
```

#### Track the same file to different target directories
```sh
# Track the same file to different target directories (no conflict)
git fetch-file add https://github.com/user/project.git config.json --comment "Main config"
git fetch-file add https://github.com/user/project.git config.json vendor --comment "Vendor config copy"
git fetch-file add https://github.com/user/project.git config.json tests/fixtures --comment "Test fixture config"
```

#### Handle conflicts and overrides
```sh
# This will show an error
git fetch-file add https://github.com/user/project.git utils.js
git fetch-file add https://github.com/user/project.git utils.js
# Output: fatal: 'utils.js' already tracked from https://github.com/user/project.git
#         hint: use --force to overwrite, or specify a different target directory

# Use --force to overwrite with new settings
git fetch-file add https://github.com/user/project.git utils.js -b develop --force

# Or track to a different location instead
git fetch-file add https://github.com/user/project.git utils.js backup -b develop
```

#### Remove tracked files
```sh
# Remove a simple entry
git fetch-file remove utils.js

# Remove a specific target when multiple exist
git fetch-file remove config.json vendor

# View current tracking status
git fetch-file status
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
repository access confirmed
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
git fetch-file pull  # Updates remote-tracking files to latest automatically
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

#### Remote-tracking files vs detached HEAD files
```sh
# Track a branch - gets updates when the branch moves
git fetch-file add https://github.com/user/lib.git utils.js -b develop --comment "Latest development utils"

# Track a specific commit - stays pinned forever  
git fetch-file add https://github.com/user/lib.git config.js --commit a1b2c3d --comment "Stable config"

# Track a tag - stays pinned to that release
git fetch-file add https://github.com/user/lib.git version.js --commit v2.1.0 --comment "Release version helper"

# Pull and update remote-tracking files to latest commits automatically
git fetch-file pull
# utils.js gets updated if develop branch moved forward
# config.js and version.js stay at their pinned commits
```

## Unit testing

```sh
python3 -m unittest
```

## Contributors

Thanks to the following people who have contributed to this project:

- [@khusmann](https://github.com/khusmann) - Reported concurrency bug with multiple files from same repository ([#2](https://github.com/andrewmcwattersandco/git-fetch-file/issues/2)) and issue with adding same filename from different repositories ([#5](https://github.com/andrewmcwattersandco/git-fetch-file/issues/5))
- [@wadabum](https://github.com/wadabum) - Added Windows support to unit tests
- [@mathstuf](https://github.com/mathstuf) - Requested and contributed selective file update feature ([#11](https://github.com/andrewmcwattersandco/git-fetch-file/issues/11))
- [@ilyagr](https://github.com/ilyagr) - Reported file renaming issue and suggested improvements ([#15](https://github.com/andrewmcwattersandco/git-fetch-file/issues/15))

## License
GNU General Public License v2.0
