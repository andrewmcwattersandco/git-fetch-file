# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.3.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.2...v1.3.0
[1.2.2]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.1...v1.2.2
[1.2.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/andrewmcwatters/git-fetch-file/releases/tag/v1.1.0
