# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.2.1]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/andrewmcwatters/git-fetch-file/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/andrewmcwatters/git-fetch-file/releases/tag/v1.1.0
