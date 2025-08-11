#!/usr/bin/env python3

"""
git-fetch-file

A tool to fetch individual files or globs from other Git repositories,
tracking their source commit in a .git-remote-files manifest.
"""

import configparser
import subprocess
import sys
import os
import shutil
from pathlib import Path
import glob as glob_module
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile

REMOTE_FILE_MANIFEST = ".git-remote-files"
CACHE_DIR = ".git/fetch-file-cache"
TEMP_DIR = ".git/fetch-file-temp"


def load_remote_files():
    """Load the .git-remote-files manifest."""
    config = configparser.ConfigParser()
    if os.path.exists(REMOTE_FILE_MANIFEST):
        config.read(REMOTE_FILE_MANIFEST)
    return config


def save_remote_files(config):
    """Write the .git-remote-files manifest to disk."""
    from io import StringIO
    output = StringIO()
    config.write(output)
    content = output.getvalue().rstrip() + '\n'
    with open(REMOTE_FILE_MANIFEST, "w") as f:
        f.write(content)


def hash_file(path):
    """Return the SHA-1 hash of a file, or None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def resolve_commit_ref(repository, commit_ref):
    """
    Resolve a commit reference to an actual commit hash.
    
    Args:
        repository (str): Remote repository URL.
        commit_ref (str): Commit reference (commit hash, branch, tag, or "HEAD").
        
    Returns:
        str: The resolved commit hash.
        
    Raises:
        subprocess.CalledProcessError: If the commit reference cannot be resolved.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, commit_ref],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            # Extract the commit hash from ls-remote output
            return result.stdout.strip().split('\t')[0]
        else:
            # If ls-remote didn't find the ref, try HEAD
            result = subprocess.run(
                ["git", "ls-remote", repository, "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip().split('\t')[0]
    except subprocess.CalledProcessError:
        raise


def add_file(repository, path, commit=None, branch=None, glob=None, comment="", target_dir=None, dry_run=False):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repository (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Specific commit hash to detach at (legacy parameter, use --detach).
        branch (str, optional): Branch or tag name to track. Defaults to HEAD.
        glob (bool, optional): Whether path is a glob pattern. Auto-detected if None.
        comment (str): Optional comment describing the file.
        target_dir (str, optional): Target directory to place the file. Defaults to same path.
        dry_run (bool): If True, only show what would be done without executing.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    # Determine the commit reference and branch tracking behavior
    # Priority: explicit commit > explicit branch > default to HEAD
    commit_ref = commit or branch or "HEAD"
    is_tracking_branch = branch is not None and not commit
    
    if dry_run:
        print(f"Would validate repository access: {repository}")
        # In dry-run mode, try to validate the repository exists
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", repository],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                print(f"error: cannot access repository: {result.stderr.strip()}")
                return
            else:
                print("repository access confirmed")
        except subprocess.TimeoutExpired:
            print("warning: repository validation timed out")
        except Exception as e:
            print(f"warning: could not validate repository: {e}")
    
    config = load_remote_files()
    section = f'file "{path}"'
    
    if dry_run:
        # Resolve the commit reference to show accurate dry-run information
        try:
            actual_commit = resolve_commit_ref(repository, commit_ref)
        except subprocess.CalledProcessError as e:
            print(f"error: failed to resolve commit reference '{commit_ref}' in repository {repository}: {e}")
            return
        
        action = "update" if section in config.sections() else "add"
        pattern_type = "glob pattern" if (glob if glob is not None else is_glob_pattern(path)) else "file"
        target_info = f" -> {target_dir}" if target_dir else ""
        
        # Create git status-like message for dry run
        # Show the resolved commit hash and whether we're tracking a branch
        if is_tracking_branch:
            short_commit = actual_commit[:7] if len(actual_commit) > 7 else actual_commit
            status_msg = f"On branch {branch} at {short_commit}"
        else:
            short_commit = actual_commit[:7] if len(actual_commit) > 7 else actual_commit
            status_msg = f"HEAD detached at {short_commit}"
        
        print(f"Would {action} {pattern_type} {path}{target_info} from {repository} ({status_msg})")
        if comment:
            print(f"With comment: {comment}")
        return
    
    # Resolve the commit reference to an actual commit hash
    try:
        actual_commit = resolve_commit_ref(repository, commit_ref)
    except subprocess.CalledProcessError as e:
        print(f"error: failed to resolve commit reference '{commit_ref}' in repository {repository}: {e}")
        return
    
    if section not in config.sections():
        config.add_section(section)
    config[section]["repository"] = repository
    
    # Always store the resolved commit hash, never a branch name
    config[section]["commit"] = actual_commit
    
    # Defensive check: ensure we're not storing a branch name as commit
    if not (len(actual_commit) == 40 and all(c in '0123456789abcdef' for c in actual_commit.lower())):
        print(f"warning: commit value '{actual_commit}' does not look like a valid hash")
    
    # Set branch tracking information
    if is_tracking_branch:
        config[section]["branch"] = branch
    elif "branch" in config[section]:
        # Remove branch key if we're now detaching to a specific commit
        del config[section]["branch"]
    
    # Only set glob property if explicitly specified
    if glob is not None:
        config[section]["glob"] = str(glob).lower()
    else:
        # Auto-detect for display purposes only
        glob = is_glob_pattern(path)
    
    if target_dir:
        # Normalize target directory
        target_dir = target_dir.rstrip('/')
        config[section]["target"] = target_dir
    
    if comment:
        config[section]["comment"] = comment
    save_remote_files(config)
    
    pattern_type = "glob pattern" if glob else "file"
    target_info = f" -> {target_dir}" if target_dir else ""
    
    # Create git status-like message using the actual commit hash
    if is_tracking_branch:
        status_msg = f"On branch {branch}"
        # Show current commit if it differs from branch name
        short_commit = actual_commit[:7] if len(actual_commit) > 7 else actual_commit
        status_msg += f" at {short_commit}"
    else:
        # Show the actual commit hash
        short_commit = actual_commit[:7] if len(actual_commit) > 7 else actual_commit
        status_msg = f"HEAD detached at {short_commit}"
    
    print(f"Added {pattern_type} {path}{target_info} from {repository} ({status_msg})")


def fetch_file(repository, path, commit, is_glob=False, force=False, target_dir=None, dry_run=False):
    """
    Fetch a single file or glob from a remote repository at a specific commit.

    Args:
        repository (str): Remote repository URL.
        path (str): File path or glob.
        commit (str): Commit, branch, or tag.
        is_glob (bool): Whether path is a glob pattern.
        force (bool): Whether to overwrite local changes.
        target_dir (str, optional): Target directory to place the file.
        dry_run (bool): If True, only show what would be done without executing.

    Returns:
        str: The commit fetched (or would be fetched in dry-run mode).
    """
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

    fetched_commit = commit

    # In dry-run mode, we skip most of the actual work since pull_files handles the summary
    if dry_run:
        return fetched_commit

    # Clone the repository once for all files
    clone_dir = Path(TEMP_DIR) / "fetch_clone"
    clone_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Clone the repository - handle commit hashes differently than branches/tags
        if commit == "HEAD" or not commit:
            # Simple clone for HEAD
            clone_cmd = ["git", "clone", "--depth", "1", repository, str(clone_dir)]
            subprocess.run(clone_cmd, capture_output=True, check=True)
        else:
            # Check if commit looks like a hash (40 hex chars) or is a branch/tag
            is_commit_hash = len(commit) == 40 and all(c in '0123456789abcdef' for c in commit.lower())
            
            if is_commit_hash:
                # For commit hashes, clone without depth and then checkout
                clone_cmd = ["git", "clone", repository, str(clone_dir)]
                subprocess.run(clone_cmd, capture_output=True, check=True)
                
                # Checkout the specific commit
                subprocess.run(
                    ["git", "checkout", commit],
                    cwd=clone_dir,
                    capture_output=True,
                    check=True
                )
            else:
                # For branches/tags, use --branch with --depth
                clone_cmd = ["git", "clone", "--depth", "1", "--branch", commit, repository, str(clone_dir)]
                subprocess.run(clone_cmd, capture_output=True, check=True)
        
        # Get the actual commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=clone_dir,
            capture_output=True,
            text=True,
            check=True
        )
        fetched_commit = result.stdout.strip()
        
        # Determine which files to process
        files = [path]
        if is_glob:
            # Get list of files from the cloned repository
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=clone_dir,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            files = [f for f in result.stdout.splitlines() if glob_module.fnmatch.fnmatch(f, path)]
            
            if files:
                print(f"Found {len(files)} files matching '{path}' in {repository}")
            else:
                print(f"No files found matching '{path}' in {repository}")
                return fetched_commit
        
        # Process all files from the same clone
        for f in files:
            # Ensure we're working with relative paths to avoid system directory conflicts
            relative_path = f.lstrip('/')
            
            # Determine target path based on target_dir
            if target_dir:
                if is_glob:
                    # For glob patterns, preserve directory structure within target directory
                    target_path = Path(target_dir) / relative_path
                    cache_key = f"{target_dir}_{relative_path}".replace("/", "_")
                else:
                    # For single files, place file in target directory, preserving filename
                    filename = Path(relative_path).name
                    target_path = Path(target_dir) / filename
                    cache_key = f"{target_dir}_{filename}".replace("/", "_")
            else:
                # Use original path structure
                target_path = Path(relative_path)
                cache_key = relative_path.replace("/", "_")
            
            cache_file = Path(CACHE_DIR) / cache_key
            local_hash = hash_file(target_path)
            last_hash = None
            if cache_file.exists():
                with open(cache_file) as cf:
                    last_hash = cf.read().strip()

            if local_hash and local_hash != last_hash and not force:
                print(f"Skipping {relative_path}: local changes detected. Use --force to overwrite.")
                continue

            # Copy the file from clone to target location
            source_file = clone_dir / f
            if source_file.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_path)
                
                new_hash = hash_file(target_path)
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_file, "w") as cf:
                    cf.write(new_hash)

                print(f"Fetched {relative_path} -> {target_path} at {commit}")
            else:
                print(f"warning: file {f} not found in repository")
    
    except subprocess.CalledProcessError as e:
        print(f"fatal: failed to clone repository: {e}")
        raise RuntimeError(f"failed to clone repository: {e}")
    finally:
        # Clean up clone directory
        if clone_dir.exists():
            shutil.rmtree(clone_dir)
        
        # Clean up temp directory contents
        if temp_dir.exists():
            for item in temp_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            # Only remove temp_dir if it's empty
            try:
                temp_dir.rmdir()
            except OSError:
                pass  # Directory not empty, that's okay

    return fetched_commit



def pull_files(force=False, save=False, dry_run=False, jobs=None, commit_message=None, edit=False, no_commit=False, auto_commit=False):
    """
    Pull all tracked files from .git-remote-files.

    Args:
        force (bool): Overwrite local changes if True.
        save (bool): Update .git-remote-files commit entries for branch-based files.
        dry_run (bool): If True, only show what would be done without executing.
        jobs (int): Number of concurrent jobs for fetching files.
        commit_message (str, optional): Custom commit message for auto-commit.
        edit (bool): Whether to open editor for commit message.
        no_commit (bool): If True, don't auto-commit changes.
        auto_commit (bool): If True, auto-commit with default message.
    """
    config = load_remote_files()
    
    if not config.sections():
        if dry_run:
            print("No remote files tracked.")
        return
    
    # Collect file entries to process
    file_entries = []
    config_migrated = False
    for section in config.sections():
        path = section.split('"')[1]
        repository = get_repository_from_config(config, section)
        
        # Migrate section if needed
        if migrate_config_section(config, section):
            config_migrated = True
            
        commit = config[section]["commit"]  # Should always exist now
        branch = config[section].get("branch", None)
        target_dir = config[section].get("target", None)
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        file_entries.append({
            'section': section,
            'path': path,
            'repository': repository,
            'commit': commit,
            'branch': branch,
            'target_dir': target_dir,
            'is_glob': is_glob
        })
    
    # Group files by repository and commit to avoid concurrent cloning of the same repo
    repository_groups = {}
    for entry in file_entries:
        repository_key = (entry['repository'], entry['commit'])
        if repository_key not in repository_groups:
            repository_groups[repository_key] = []
        repository_groups[repository_key].append(entry)
    
    # Collect results for organized dry-run output
    if dry_run:
        would_fetch = []
        would_skip = []
        up_to_date = []
        errors = []
        
        for entry in file_entries:
            # Check if file would be updated by examining local state
            try:
                # Determine target path based on target_dir
                if entry['target_dir']:
                    if entry['is_glob']:
                        # For glob patterns, preserve directory structure within target directory
                        target_path = Path(entry['target_dir']) / entry['path']
                        cache_key = f"{entry['target_dir']}_{entry['path']}".replace("/", "_")
                    else:
                        # For single files, place file in target directory, preserving filename
                        filename = Path(entry['path']).name
                        target_path = Path(entry['target_dir']) / filename
                        cache_key = f"{entry['target_dir']}_{filename}".replace("/", "_")
                else:
                    target_path = Path(entry['path'])
                    cache_key = entry['path'].replace("/", "_")
                
                cache_file = Path(CACHE_DIR) / cache_key
                local_hash = hash_file(target_path)
                last_hash = None
                if cache_file.exists():
                    with open(cache_file) as cf:
                        last_hash = cf.read().strip()
                
                # Simulate what would happen
                if local_hash and local_hash != last_hash and not force:
                    would_skip.append(f"{entry['path']} from {entry['repository']}")
                elif local_hash == last_hash and not entry.get('branch'):
                    # File exists and hash matches, and it's at a specific commit - up to date
                    short_commit = entry['commit'][:7] if len(entry['commit']) > 7 else entry['commit']
                    status_display = f"HEAD detached at {short_commit}"
                    up_to_date.append(f"{entry['path']} from {entry['repository']} ({status_display})")
                else:
                    # Would fetch - show commit change if applicable
                    if entry.get('branch'):
                        # Branch-tracked file - like "On branch main"
                        status_info = f"On branch {entry['branch']}"
                        if save:
                            status_info += " -> [update to latest]"
                    else:
                        # Non-branch-tracked files
                        short_commit = entry['commit'][:7] if len(entry['commit']) > 7 else entry['commit']
                        status_info = f"HEAD detached at {short_commit}"
                    would_fetch.append(f"{entry['path']} from {entry['repository']} ({status_info})")
                        
            except Exception as e:
                errors.append(f"{entry['path']} from {entry['repository']}: {str(e)}")
        
        # Print organized output
        if would_fetch:
            print("Would fetch:")
            for item in would_fetch:
                print(f"  {item}")
            print()
        
        if would_skip:
            print("Would skip (local changes):")
            for item in would_skip:
                print(f"  {item} (use --force to overwrite)")
            print()
        
        if up_to_date:
            print("Up to date:")
            for item in up_to_date:
                print(f"  {item}")
            print()
        
        if errors:
            print("Errors:")
            for item in errors:
                print(f"  {item}")
            print()
        
        if not (would_fetch or would_skip or up_to_date or errors):
            print("No changes needed.")
        
        return
    
    # Execute concurrent fetching by repository groups
    def fetch_repository_group(repository_key, entries):
        """Fetch all files from a single repository group as a batch."""
        repository, commit = repository_key
        results = []
        
        temp_dir = Path(TEMP_DIR)
        temp_dir.mkdir(exist_ok=True)
        Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

        # Create a unique clone directory for this repo+commit combination
        repository_hash = hashlib.sha1(f"{repository}#{commit}".encode()).hexdigest()[:8]
        clone_dir = Path(TEMP_DIR) / f"fetch_clone_{repository_hash}"
        
        try:
            # Clone the repository once for all files in this group
            clone_dir.mkdir(parents=True, exist_ok=True)
            
            # Clone the repository - handle commit hashes differently than branches/tags
            if commit == "HEAD" or not commit:
                # Simple clone for HEAD
                clone_cmd = ["git", "clone", "--depth", "1", repository, str(clone_dir)]
                subprocess.run(clone_cmd, capture_output=True, check=True)
            else:
                # Check if commit looks like a hash (40 hex chars) or is a branch/tag
                is_commit_hash = len(commit) == 40 and all(c in '0123456789abcdef' for c in commit.lower())
                
                if is_commit_hash:
                    # For commit hashes, clone without depth and then checkout
                    clone_cmd = ["git", "clone", repository, str(clone_dir)]
                    subprocess.run(clone_cmd, capture_output=True, check=True)
                    
                    # Checkout the specific commit
                    subprocess.run(
                        ["git", "checkout", commit],
                        cwd=clone_dir,
                        capture_output=True,
                        check=True
                    )
                else:
                    # For branches/tags, use --branch with --depth
                    clone_cmd = ["git", "clone", "--depth", "1", "--branch", commit, repository, str(clone_dir)]
                    subprocess.run(clone_cmd, capture_output=True, check=True)
            
            # Get the actual commit hash
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=clone_dir,
                capture_output=True,
                text=True,
                check=True
            )
            fetched_commit = result.stdout.strip()
            
            # Process each file entry
            for entry in entries:
                try:
                    path = entry['path']
                    is_glob = entry['is_glob']
                    target_dir = entry['target_dir']
                    
                    # Determine which files to process
                    files = [path]
                    if is_glob:
                        # Get list of files from the cloned repository
                        result = subprocess.run(
                            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                            cwd=clone_dir,
                            capture_output=True,
                            text=True
                        )
                        if result.returncode != 0:
                            raise RuntimeError(result.stderr)
                        files = [f for f in result.stdout.splitlines() if glob_module.fnmatch.fnmatch(f, path)]
                        
                        if files:
                            print(f"Found {len(files)} files matching '{path}' in {repository}")
                        else:
                            print(f"No files found matching '{path}' in {repository}")
                            results.append({
                                'section': entry['section'],
                                'path': entry['path'],
                                'commit': entry['commit'],
                                'fetched_commit': fetched_commit,
                                'success': True,
                                'error': None
                            })
                            continue
                    
                    # Process all files for this entry
                    for f in files:
                        # Ensure we're working with relative paths to avoid system directory conflicts
                        relative_path = f.lstrip('/')
                        
                        # Determine target path based on target_dir
                        if target_dir:
                            if is_glob:
                                # For glob patterns, preserve directory structure within target directory
                                target_path = Path(target_dir) / relative_path
                                cache_key = f"{target_dir}_{relative_path}".replace("/", "_")
                            else:
                                # For single files, place file in target directory, preserving filename
                                filename = Path(relative_path).name
                                target_path = Path(target_dir) / filename
                                cache_key = f"{target_dir}_{filename}".replace("/", "_")
                        else:
                            # Use original path structure
                            target_path = Path(relative_path)
                            cache_key = relative_path.replace("/", "_")
                        
                        cache_file = Path(CACHE_DIR) / cache_key
                        local_hash = hash_file(target_path)
                        last_hash = None
                        if cache_file.exists():
                            with open(cache_file) as cf:
                                last_hash = cf.read().strip()

                        if local_hash and local_hash != last_hash and not force:
                            print(f"Skipping {relative_path}: local changes detected. Use --force to overwrite.")
                            continue

                        # Copy the file from clone to target location
                        source_file = clone_dir / f
                        if source_file.exists():
                            target_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(source_file, target_path)
                            
                            new_hash = hash_file(target_path)
                            cache_file.parent.mkdir(parents=True, exist_ok=True)
                            with open(cache_file, "w") as cf:
                                cf.write(new_hash)

                            print(f"Fetched {relative_path} -> {target_path} at {commit}")
                        else:
                            print(f"warning: file {f} not found in repository")
                    
                    results.append({
                        'section': entry['section'],
                        'path': entry['path'],
                        'commit': entry['commit'],
                        'fetched_commit': fetched_commit,
                        'success': True,
                        'error': None
                    })
                    
                except Exception as e:
                    results.append({
                        'section': entry['section'],
                        'path': entry['path'],
                        'commit': entry['commit'],
                        'fetched_commit': None,
                        'success': False,
                        'error': str(e)
                    })
                    
        except Exception as e:
            # If the entire repo group fails, mark all entries as failed
            for entry in entries:
                results.append({
                    'section': entry['section'],
                    'path': entry['path'],
                    'commit': entry['commit'],
                    'fetched_commit': None,
                    'success': False,
                    'error': str(e)
                })
        finally:
            # Clean up clone directory
            if clone_dir.exists():
                shutil.rmtree(clone_dir)
            
            # Clean up temp directory contents
            if temp_dir.exists():
                for item in temp_dir.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir() and item.name.startswith("fetch_clone_"):
                        # Only remove clone directories that match our pattern
                        try:
                            shutil.rmtree(item)
                        except Exception:
                            pass
                # Only remove temp_dir if it's empty
                try:
                    temp_dir.rmdir()
                except OSError:
                    pass  # Directory not empty, that's okay
        
        return results
    
    # Determine default jobs if not set (limit to number of repo groups for efficiency)
    if jobs is None:
        try:
            jobs = min(os.cpu_count() or 1, len(repository_groups))
        except Exception:
            jobs = 1
    else:
        jobs = min(jobs, len(repository_groups))
    
    all_results = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        # Submit tasks for each repository group
        future_to_repository = {executor.submit(fetch_repository_group, repository_key, entries): repository_key 
                         for repository_key, entries in repository_groups.items()}
        
        # Collect results as they complete
        for future in as_completed(future_to_repository):
            repository_results = future.result()
            all_results.extend(repository_results)
            
            # Print errors immediately for better user feedback
            for result in repository_results:
                if not result['success']:
                    print(f"error: fetching {result['path']}: {result['error']}")
    
    # Update config with new commits if save is enabled
    config_needs_save = config_migrated  # Save if we migrated any sections
    if save:
        updated = False
        for result in all_results:
            if result['success'] and result['commit'] != "HEAD":
                if not result['commit'].startswith(result['fetched_commit'][:7]):
                    config[result['section']]["commit"] = result['fetched_commit']
                    updated = True
        
        if updated:
            config_needs_save = True
    
    # Save config if needed (migration or updates)
    if config_needs_save:
        save_remote_files(config)
    
    # Auto-commit changes if requested (and not in dry-run mode)
    if not dry_run:
        # Check if we should commit
        should_commit = not no_commit and (commit_message is not None or edit or auto_commit)
        
        # If no explicit commit flags were used, default to no commit (like git merge)
        if not should_commit and commit_message is None and not edit and not auto_commit and not no_commit:
            should_commit = False
        
        # Commit if requested
        if should_commit:
            # Pass full results for informative commit message generation
            commit_changes(commit_message=commit_message, edit=edit, no_commit=no_commit, file_results=all_results)


def status_files():
    """Print all files tracked in .git-remote-files."""
    config = load_remote_files()
    
    if not config.sections():
        print("No remote files tracked.")
        return
    
    config_migrated = False
    for section in config.sections():
        path = section.split('"')[1]
        repository = get_repository_from_config(config, section)
        
        # Migrate section if needed
        if migrate_config_section(config, section):
            config_migrated = True
            
        commit = config[section]["commit"]  # Should always exist now
        target_dir = config[section].get("target", None)
        comment = config[section].get("comment", "")
        branch = config[section].get("branch", None)
        
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        # Format the path display like git remote -v
        path_display = path
        if target_dir:
            path_display += f" -> {target_dir}"
        
        # Add glob indicator
        glob_indicator = " (glob)" if is_glob else ""
        
        # Determine tracking status and display format like git status
        if branch:
            # This is a branch-tracking entry - like "On branch main"
            status_display = f"On branch {branch}"
            # Always show the current commit hash since commit is always a hash now
            short_commit = commit[:7] if len(commit) > 7 else commit
            status_display += f" at {short_commit}"
        else:
            # This is a non-branch-tracked entry
            # Commit should always be a hash now, never "HEAD"
            short_commit = commit[:7] if len(commit) > 7 else commit
            status_display = f"HEAD detached at {short_commit}"
        
        # Format like: path[glob_indicator] repository (status_display)
        line = f"{path_display}{glob_indicator}\t{repository} ({status_display})"
        
        # Add comment if present
        if comment:
            line += f" # {comment}"
        
        print(line)
    
    # Save config if any sections were migrated
    if config_migrated:
        save_remote_files(config)


def is_glob_pattern(path):
    """Check if a path contains glob pattern characters."""
    glob_chars = ['*', '?', '[', ']', '{', '}']
    return any(char in path for char in glob_chars)


def is_git_repository():
    """Check if we're in a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def has_git_changes():
    """Check if there are any changes staged or unstaged in the git repository."""
    try:
        # Check for staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            return True
        
        # Check for unstaged changes to tracked files
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            return True
        
        # Check for untracked files that might have been fetched
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True
        )
        untracked = result.stdout.strip().split('\n') if result.stdout.strip() else []
        
        # Filter to only check files that might be from git-fetch-file
        # (this is a heuristic - we assume newly fetched files are untracked)
        return len(untracked) > 0
        
    except subprocess.CalledProcessError:
        return False


def commit_changes(commit_message=None, edit=False, no_commit=False, file_results=None):
    """
    Commit changes to git if there are any.
    
    Args:
        commit_message (str, optional): Custom commit message. If None, uses default.
        edit (bool): Whether to open editor for commit message.
        no_commit (bool): If True, don't commit (useful for overriding default behavior).
        file_results (list, optional): List of file fetch results for informative default message.
    
    Returns:
        bool: True if commit was made, False otherwise.
    """
    if no_commit:
        return False
    
    if not is_git_repository():
        print("warning: not in a git repository, skipping commit")
        return False
    
    if not has_git_changes():
        return False
    
    try:
        # Stage all changes (including new files)
        subprocess.run(["git", "add", "."], check=True)
        
        # Prepare commit command
        commit_cmd = ["git", "commit"]
        
        if edit:
            # Use editor for commit message
            if commit_message:
                # Pre-populate editor with provided message
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    f.write(commit_message)
                    temp_file = f.name
                try:
                    commit_cmd.extend(["-t", temp_file])
                    subprocess.run(commit_cmd, check=True)
                finally:
                    os.unlink(temp_file)
            else:
                # Just open editor
                subprocess.run(commit_cmd, check=True)
        else:
            # Use provided message or generate git-style informative default
            if not commit_message:
                commit_message = generate_default_commit_message(file_results)
            commit_cmd.extend(["-m", commit_message])
            subprocess.run(commit_cmd, check=True)
        
        print(f"Committed changes: {commit_message if not edit else '[via editor]'}")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"warning: failed to commit changes: {e}")
        return False


def generate_default_commit_message(file_results):
    """
    Generate a git-style informative default commit message based on what was fetched.
    
    Args:
        file_results (list): List of file fetch results.
    
    Returns:
        str: Generated commit message.
    """
    if not file_results:
        return "Update remote files"
    
    successful_results = [r for r in file_results if r['success']]
    
    if not successful_results:
        return "Update remote files"
    
    # Count files
    file_count = len(successful_results)
    
    if file_count == 1:
        result = successful_results[0]
        file_path = result['path']
        commit_hash = result.get('fetched_commit', '')
        
        # Extract just the filename for cleaner display
        if '/' in file_path:
            file_name = file_path.split('/')[-1]
        else:
            file_name = file_path
        
        # Include commit info if available and it's not HEAD
        if commit_hash and len(commit_hash) >= 7 and commit_hash != result.get('commit', ''):
            short_hash = commit_hash[:7]
            return f"Update {file_name} to {short_hash}"
        else:
            return f"Update {file_name}"
    
    elif file_count <= 3:
        # List individual files for small counts
        file_names = []
        for result in successful_results:
            file_path = result['path']
            if '/' in file_path:
                file_name = file_path.split('/')[-1]
            else:
                file_name = file_path
            file_names.append(file_name)
        
        if file_count == 2:
            return f"Update {file_names[0]} and {file_names[1]}"
        else:  # file_count == 3
            return f"Update {', '.join(file_names[:-1])}, and {file_names[-1]}"
    
    else:
        # For larger counts, use summary format with directory info if applicable
        # Check if files are from the same directory structure
        dirs = set()
        for result in successful_results:
            file_path = result['path']
            if '/' in file_path:
                dir_path = '/'.join(file_path.split('/')[:-1])
                dirs.add(dir_path)
        
        if len(dirs) == 1 and dirs != {''}:
            dir_name = dirs.pop()
            return f"Update {file_count} files in {dir_name}/"
        else:
            return f"Update {file_count} remote files"


def get_repository_from_config(config, section):
    """
    Get repository URL from config section with backward compatibility.
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        str: Repository URL
    """
    # Try new 'repository' key first, fall back to legacy 'repo' key
    if "repository" in config[section]:
        return config[section]["repository"]
    elif "repo" in config[section]:
        return config[section]["repo"]
    else:
        raise KeyError(f"No repository URL found in section {section}")


def migrate_config_section(config, section):
    """
    Migrate a config section from legacy format to current format.
    This includes:
    1. Migrating 'repo' key to 'repository' key
    2. Moving branch names from 'commit' field to 'branch' key, resolving commit to hash
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        bool: True if migration occurred, False if already using new format
    """
    migrated = False
    
    # Migrate repo -> repository
    if "repo" in config[section] and "repository" not in config[section]:
        config[section]["repository"] = config[section]["repo"]
        del config[section]["repo"]
        migrated = True
    
    # Check if commit field contains a branch name instead of a commit hash
    if "commit" in config[section] and "branch" not in config[section]:
        commit_value = config[section]["commit"]
        repository = get_repository_from_config(config, section)
        
        # Check if commit looks like a hash (40 hex chars) or might be a branch/tag
        is_likely_hash = (len(commit_value) == 40 and 
                         all(c in '0123456789abcdef' for c in commit_value.lower()))
        
        # If it doesn't look like a hash, or if it's "HEAD", treat it as a branch reference
        if not is_likely_hash or commit_value == "HEAD":
            try:
                actual_commit = resolve_commit_ref(repository, commit_value)
                if actual_commit != commit_value:
                    # This was a branch/tag reference, not a commit hash
                    config[section]["branch"] = commit_value  # Store the original branch name
                    config[section]["commit"] = actual_commit  # Store the resolved commit hash
                    migrated = True
                    print(f"Migrated '{commit_value}' from commit to branch tracking with hash '{actual_commit[:7]}' for {section}")
            except subprocess.CalledProcessError as e:
                # If we can't resolve the commit, leave it as-is but warn
                print(f"warning: could not resolve commit reference '{commit_value}' for {section}: {e}")
    
    return migrated


def main():
    """Command-line interface for git-fetch-file."""
    if len(sys.argv) < 2:
        print("Usage: git fetch-file <command> [args...]")
        print("Commands:")
        print("  add <repository> <path> [target_dir] [options]  Add a file or glob to track")
        print("  pull [options]                            Pull all tracked files")
        print("  list                                      List all tracked files")
        print("  status                                    Alias for list")
        print("")
        print("Add options:")
        print("  --detach <commit>                         Track specific commit/tag (detached)")
        print("  --commit <commit>                         Alias for --detach (for compatibility)")
        print("  -b, --branch <branch>                     Track specific branch")
        print("  --glob                                    Force treat path as glob pattern")
        print("  --no-glob                                 Force treat path as literal file")
        print("  --comment <text>                          Add descriptive comment")
        print("  --dry-run                                 Show what would be done")
        print("")
        print("Pull options:")
        print("  --dry-run                                 Show what would be done without executing")
        print("  --force                                   Overwrite local changes")
        print("  --save                                    Update commit hashes for branches")
        print("  --jobs=<n>                                Number of parallel jobs (default: auto)")
        print("  --commit                                  Auto-commit with default message")
        print("  -m <msg>, --message=<msg>                 Commit with message")
        print("  --edit                                    Edit commit message")
        print("  --no-commit                               Don't auto-commit changes")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: git fetch-file add <repository> <path> [target_dir] [--detach <commit>] [--commit <commit>] [-b|--branch <branch>] [--glob] [--no-glob] [--comment <text>] [--dry-run]")
            sys.exit(1)
        repository = sys.argv[2]
        path = sys.argv[3]
        
        # Check if the 4th argument is a target directory (doesn't start with --)
        target_dir = None
        args_start = 4
        if len(sys.argv) > 4 and not sys.argv[4].startswith("--"):
            target_dir = sys.argv[4]
            args_start = 5
        
        commit = None
        branch = None
        glob_flag = None  # None means auto-detect
        comment = ""
        dry_run = False
        args = sys.argv[args_start:]
        i = 0
        while i < len(args):
            if args[i] == "--detach" or args[i] == "--commit":
                i += 1
                commit = args[i]
            elif args[i] == "--branch" or args[i] == "-b":
                i += 1
                branch = args[i]
            elif args[i] == "--glob":
                glob_flag = True
            elif args[i] == "--no-glob":
                glob_flag = False
            elif args[i] == "--comment":
                i += 1
                comment = args[i]
            elif args[i] == "--dry-run":
                dry_run = True
            i += 1
        add_file(repository, path, commit, branch, glob_flag, comment, target_dir, dry_run)

    elif cmd == "pull":
        force_flag = "--force" in sys.argv
        save_flag = "--save" in sys.argv
        dry_run_flag = "--dry-run" in sys.argv
        edit_flag = "--edit" in sys.argv
        no_commit_flag = "--no-commit" in sys.argv
        auto_commit_flag = "--commit" in sys.argv
        
        # Parse commit message from -m or --message
        commit_message = None
        for i, arg in enumerate(sys.argv):
            if arg == "-m" and i + 1 < len(sys.argv):
                commit_message = sys.argv[i + 1]
                break
            elif arg.startswith("--message="):
                commit_message = arg.split("=", 1)[1]
                break
        
        # Parse --jobs parameter
        jobs = None  # default: None means auto-detect (os.cpu_count())
        for arg in sys.argv:
            if arg.startswith("--jobs="):
                try:
                    jobs = int(arg.split("=")[1])
                    if jobs < 1:
                        print("error: --jobs must be a positive integer")
                        sys.exit(1)
                except ValueError:
                    print("error: --jobs must be a positive integer")
                    sys.exit(1)
        pull_files(force=force_flag, save=save_flag, dry_run=dry_run_flag, jobs=jobs,
                  commit_message=commit_message, edit=edit_flag, no_commit=no_commit_flag,
                  auto_commit=auto_commit_flag)

    elif cmd == "status" or cmd == "list":
        status_files()

    else:
        print(f"fatal: unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
