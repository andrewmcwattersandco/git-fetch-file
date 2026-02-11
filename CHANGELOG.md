# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-02-10

### Added
- **Go implementation**: Complete port of git-fetch-file to Go for significantly improved performance
- **Native binary**: Compiled Go binary for faster execution without Python interpreter overhead

### Deprecated
- **Python implementation**: Now deprecated in favor of the Go version. Users are encouraged to migrate to the Go binary for improved performance and continued updates. The Python version remains compatible but will receive limited maintenance.

### Technical Details
- Zero external dependencies - uses only Go standard library
- Concurrent file fetching with goroutines for optimal performance
- Compatible with all existing `.git-remote-files` manifest files
- Feature parity with Python implementation including all commands: add, pull, status, remove
- Full support for glob patterns, branch tracking, dry-run mode, and all command-line options

### Breaking Changes
- None - Go implementation maintains full backward compatibility with Python version
- Both implementations can be used interchangeably

### Notes
- Python implementation is deprecated but remains compatible for existing users
- Users are encouraged to migrate to the Go binary for improved performance and continued updates
- Python version will receive limited maintenance going forward
- Benchmark results vary by system; run `go test -bench=.` to test on your hardware
- Go binary requires Go 1.21 or later to build

## [1.4.6] - 2025-11-30

### Fixed
- **Dotfile renaming**: Fixed bug where dotfiles (like `.gitignore`, `.env`, etc.) were incorrectly treated as directories when used as target paths. Commands like `git fetch-file add <repo> Node.gitignore .gitignore` now correctly rename the file instead of placing it in a `.gitignore/` directory.

### Technical Details
- Enhanced `get_target_path_and_cache_key()` heuristic to recognize dotfiles (files starting with `.`) as files rather than directories
- Added special case handling: files are now identified by either having a file extension OR being a dotfile

## [1.4.5] - 2025-11-30

### Contributors
- [@ilyagr](https://github.com/ilyagr) - Reported file renaming issue and suggested improvements ([#15](https://github.com/andrewmcwattersandco/git-fetch-file/issues/15))

## [1.4.4] - 2025-11-30

### Added
- **Selective file updates**: Added `-r/--repository` and `-p/--path` flags to `pull` command for updating specific subsets of tracked files ([#11](https://github.com/andrewmcwattersandco/git-fetch-file/issues/11))

### Contributors
- [@mathstuf](https://github.com/mathstuf) - Requested and contributed selective file update feature ([#11](https://github.com/andrewmcwattersandco/git-fetch-file/issues/11))

## [1.4.3] - 2025-08-20

### Fixed
- **Target path calculation with git aliases**: Fixed critical bug where target paths were incorrectly calculated when using `git fetch-file` commands from subdirectories within a repository. Git aliases that start with `!` execute from the repository root, causing the tool to incorrectly detect the working directory as the repository root instead of the directory where the user invoked the command.
- **GIT_PREFIX environment variable support**: Now properly uses Git's `GIT_PREFIX` environment variable to determine the correct relative path from repository root to where the command was invoked, ensuring target paths are calculated correctly regardless of where the git alias runs.
- **Manifest target storage accuracy**: Target paths in the `.git-remote-files` manifest are now correctly stored relative to the git repository root, accounting for the actual directory where the command was run.

### Technical Details
- Modified `get_relative_path_from_git_root()` to use `os.environ.get('GIT_PREFIX')` instead of `Path.cwd().relative_to(git_root)`
- Enhanced conflict detection logic to handle cases where the same file from the same repository is being tracked with different target paths
- Improved target path calculation for both explicit target directories and default placement scenarios

### Examples of Fixed Behavior
Before (incorrect):
```bash
cd repo/subdir
git fetch-file add https://github.com/user/repo.git file.txt myfile.txt
# Would store: target = myfile.txt (incorrect, should be subdir/myfile.txt)
```

After (correct):
```bash
cd repo/subdir  
git fetch-file add https://github.com/user/repo.git file.txt myfile.txt
# Now stores: target = subdir/myfile.txt (correct)
```

## [1.4.2] - 2025-08-14

### Fixed
- Fixed `pull` command not updating remote-tracking files to their latest commits
- Fixed incorrect "local changes detected" warnings when pulling remote updates
- Remote-tracking files now automatically resolve to the latest commit on their branch during pulls
- Fixed grouping logic to use resolved commits instead of stored commits for remote-tracking files
- Improved local change detection to distinguish between actual user modifications and expected remote updates

### Changed
- **BREAKING**: Removed `--save` flag from `pull` command
- Remote tracking is now automatic: files with a `branch` key automatically update to latest commit, files without `branch` key remain pinned to their stored commit
- Improved user experience by making remote vs. pinned behavior more intuitive
- Added deprecated `--save` flag for backwards compatibility (shows warning and is ignored)

### Backwards Compatibility
- Scripts using `git fetch-file pull --save` will continue to work but display a deprecation warning
- The `--save` flag is now a no-op as the behavior it provided is now automatic for remote-tracking files

## [1.4.1] - 2025-08-13

### Fixed
- **Directory creation bug**: Fixed issue where `TEMP_DIR` wasn't being created before use in `pull_files` function, which could cause operations to fail
- **Test coverage**: Added test for `git fetch-file pull` command to prevent regression

## [1.4.0] - 2025-08-11

### Added
- **Multi-target file tracking**: Same file can now be tracked from different repositories or to different target directories without conflicts
- **Enhanced conflict detection**: Intelligent conflict detection only prevents duplicates when repository, file path, and target directory are all identical
- **Remove command**: New `git fetch-file remove <path> [<target_dir>]` command to remove tracked files from the manifest
- **Force flag for add command**: Added `--force` flag to `add` command for intentionally overwriting existing entries
- **Repository disambiguation**: `--repository` flag for remove command to disambiguate entries from different repositories

### Changed
- **BREAKING: Manifest format simplification**: All manifest sections now use unified format `[file "path" from "repository_url"]` for clarity and consistency
- **Eliminated redundant repository keys**: Repository information is now stored only in section names, eliminating duplicate `repository` keys in manifest entries
- **Simplified codebase**: Removed unnecessary helper functions, making the code more maintainable
- **Git-style error messages**: Conflict errors now use concise, git-like format: `fatal: 'file' already tracked from <repo>` with helpful hints
- **Backward compatibility**: Old manifest formats are still supported and automatically migrated

### Fixed
- **Issue #5: Cannot add same filename from two different repos**: Resolved conflict detection that incorrectly prevented tracking same filename from different repositories or to different target directories

### Improved
- **Conflict resolution guidance**: Error messages now provide clear options: use `--force` to overwrite, or specify different target directory
- **Status output clarity**: Status command shows multiple entries when same file is tracked to different locations
- **Documentation coverage**: Comprehensive README.md updates with conflict handling examples and new command documentation

## [1.3.0] - 2025-08-11

### Added
- **Backward compatibility for manifest format**: Automatic migration from legacy `repo` key to new `repository` key in `.git-remote-files` manifest
- **Documentation improvements**: Updated README.md with corrected manifest format examples and improved feature descriptions
- **Git-consistent options**: Added `--detach` option for commit tracking (consistent with `git checkout --detach`), while maintaining `--commit` for backward compatibility

### Changed
- **BREAKING: Manifest format standardization**: The `.git-remote-files` manifest now uses `repository` as the key instead of `repo` for consistency. Old files are automatically migrated when read
- **Commit hash storage**: The manifest now always stores actual commit hashes in the `commit` field, never symbolic references like "HEAD" or branch names
- **Dry-run accuracy**: Dry-run mode now resolves commit references to actual commit hashes before displaying output, providing accurate information about what would be stored
- **CLI argument consistency**: Updated help text and all user-facing output to use `<repository>` instead of `<repo>` for consistency

### Fixed
- **Commit reference resolution**: Fixed inconsistency where `commit_ref` could be "HEAD" in display logic, which didn't align with the requirement that manifests always store resolved commit hashes
- **Display consistency**: Eliminated confusing "HEAD" symbolic references from manifest format and user-facing output
- **Jobs parallelism documentation**: Corrected help text from "default: 4" to "default: auto" to accurately reflect the CPU-based auto-detection behavior
- **README.md accuracy**: Fixed output message casing and manifest format examples to match actual tool behavior

### Improved
- **Code organization**: Added `resolve_commit_ref()` helper function to centralize commit resolution logic and eliminate code duplication
- **User experience**: All user-facing code, variables, and help text now consistently use "repository" instead of "repo" for clarity
- **Documentation completeness**: Enhanced README.md with backward compatibility notes, corrected examples, and comprehensive feature descriptions
- **Migration transparency**: Added clear notes about v1.3.0 manifest format changes with full backward compatibility assurance
- **Git consistency**: Improved option naming with `--detach` (matching git's terminology) while preserving `--commit` for compatibility

## [1.2.2] - 2025-08-11

### Fixed
- **Concurrency crash with multiple files from same repository**: Fixed fatal crash when pulling multiple files from the same repository concurrently. The tool now groups files by repository and commit, creating a single shared clone per group instead of attempting multiple simultaneous clones to the same directory. This resolves the "Command returned non-zero exit status 128" error that occurred when using default concurrency settings

### Improved
- **Optimized concurrent fetching**: Repository cloning is now batched by repository and commit combination, reducing redundant clone operations and improving performance when fetching multiple files from the same source

## [1.2.1] - 2025-08-09

### Fixed
- **Commit hash cloning**: Fixed failure when fetching files from specific commit hashes. The tool now properly distinguishes between commit hashes (40 hex characters) and branch/tag names when cloning repositories, resolving the "returned non-zero exit status 128" error

## [1.2.0] - 2025-08-09

### Fixed
- **Directory structure preservation**: Fixed bug where glob patterns with target directories would flatten all files instead of preserving their original directory hierarchy
- **Dry-run target path calculation**: Fixed inconsistent path handling between actual fetching and dry-run mode for glob patterns

### Improved
- **Massive performance improvement for glob patterns**: Reduced repository cloning from N+1 operations (1 for discovery + 1 per file) to just 1 clone operation total
- **Git-style error messages**: Updated all error and warning messages to follow standard git command conventions:
  - Use `fatal:`, `error:`, and `warning:` prefixes appropriately
  - Lowercase message text (except proper nouns)
  - No trailing punctuation
  - Consistent tone and style with git commands

### Added
- **Informative glob feedback**: Added messages showing how many files match a glob pattern during fetching (e.g., "Found 71 files matching '**' in repository")

## [1.1.0] - Previous Release
- Initial tracked version with core functionality

[1.4.4]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.4.3...v1.4.4
[1.4.3]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.4.2...v1.4.3
[1.4.2]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.3.0...v1.4.0
[1.3.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.2...v1.3.0
[1.2.2]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/andrewmcwatters/git-fetch-file/releases/tag/v1.1.0
