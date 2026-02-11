package main

import (
	"bufio"
	"crypto/sha1"
	"encoding/hex"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
)

const (
	remoteFileManifest = ".git-remote-files"
	cacheDir           = ".git/fetch-file-cache"
	tempDir            = ".git/fetch-file-temp"
)

type ConfigSection struct {
	Path          string
	RepoURL       string
	Commit        string
	Branch        string
	Target        string
	Comment       string
	Glob          string
	ForceType     string
	FetchedCommit string // Used internally for tracking resolved commits
}

type Config struct {
	Sections map[string]*ConfigSection
}

type FileResult struct {
	Section        string
	Path           string
	Repository     string
	Commit         string
	Branch         string
	FetchedCommit  string
	FilesProcessed int
	FilesUpdated   int
	FilesUpToDate  int
	FilesSkipped   int
	Success        bool
	Error          string
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	command := os.Args[1]
	switch command {
	case "add":
		handleAdd(os.Args[2:])
	case "pull":
		handlePull(os.Args[2:])
	case "status", "list":
		handleStatus()
	case "remove":
		handleRemove(os.Args[2:])
	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Println("Usage: git-fetch-file <command> [options]")
	fmt.Println("\nCommands:")
	fmt.Println("  add       Add a file or glob to track")
	fmt.Println("  pull      Pull all tracked files")
	fmt.Println("  status    List all tracked files")
	fmt.Println("  list      Alias for status")
	fmt.Println("  remove    Remove a tracked file")
}

func handleAdd(args []string) {
	fs := flag.NewFlagSet("add", flag.ExitOnError)
	branch := fs.String("branch", "", "Track specific branch")
	branchShort := fs.String("b", "", "Track specific branch (short)")
	commit := fs.String("commit", "", "Track specific commit/tag (detached)")
	detach := fs.String("detach", "", "Track specific commit/tag (detached)")
	comment := fs.String("comment", "", "Add descriptive comment")
	dryRun := fs.Bool("dry-run", false, "Show what would be done")
	force := fs.Bool("force", false, "Overwrite existing entry")
	glob := fs.Bool("glob", false, "Force treat path as glob pattern")
	noGlob := fs.Bool("no-glob", false, "Force treat path as literal file")
	isFile := fs.Bool("is-file", false, "Force treat path as file")
	isDirectory := fs.Bool("is-directory", false, "Force treat path as directory")

	fs.Parse(args)

	if fs.NArg() < 2 {
		fmt.Fprintln(os.Stderr, "error: repository and path required")
		os.Exit(1)
	}

	repository := fs.Arg(0)
	path := fs.Arg(1)
	var targetDir string
	if fs.NArg() > 2 {
		targetDir = fs.Arg(2)
	}

	// Handle branch short flag
	if *branchShort != "" && *branch == "" {
		*branch = *branchShort
	}

	// Handle detach/commit flags
	commitRef := *detach
	if commitRef == "" {
		commitRef = *commit
	}

	// Handle glob flags
	var globFlag *bool
	if *glob && *noGlob {
		fmt.Fprintln(os.Stderr, "error: --glob and --no-glob are mutually exclusive")
		os.Exit(1)
	} else if *glob {
		trueVal := true
		globFlag = &trueVal
	} else if *noGlob {
		falseVal := false
		globFlag = &falseVal
	}

	// Handle force type flags
	var forceType string
	if *isFile && *isDirectory {
		fmt.Fprintln(os.Stderr, "error: --is-file and --is-directory are mutually exclusive")
		os.Exit(1)
	} else if *isFile {
		forceType = "file"
	} else if *isDirectory {
		forceType = "directory"
	}

	addFile(repository, path, commitRef, *branch, globFlag, *comment, targetDir, *dryRun, *force, forceType)
}

func handlePull(args []string) {
	fs := flag.NewFlagSet("pull", flag.ExitOnError)
	force := fs.Bool("force", false, "Overwrite local changes")
	dryRun := fs.Bool("dry-run", false, "Show what would be done")
	jobs := fs.Int("jobs", 0, "Number of parallel jobs")
	commitMsg := fs.String("message", "", "Commit with message")
	commitMsgShort := fs.String("m", "", "Commit with message (short)")
	edit := fs.Bool("edit", false, "Edit commit message")
	noCommit := fs.Bool("no-commit", false, "Don't auto-commit changes")
	autoCommit := fs.Bool("commit", false, "Auto-commit with default message")
	save := fs.Bool("save", false, "(Deprecated)")
	repo := fs.String("repository", "", "Limit to files from repository")
	repoShort := fs.String("r", "", "Limit to files from repository (short)")

	fs.Parse(args)

	// Handle message short flag
	if *commitMsgShort != "" && *commitMsg == "" {
		*commitMsg = *commitMsgShort
	}

	// Handle repo short flag
	if *repoShort != "" && *repo == "" {
		*repo = *repoShort
	}

	if *save {
		fmt.Fprintln(os.Stderr, "warning: --save is deprecated. Remote-tracking files now update automatically.")
	}

	pullFiles(*force, *dryRun, *jobs, *commitMsg, *edit, *noCommit, *autoCommit, *repo)
}

func handleStatus() {
	statusFiles()
}

func handleRemove(args []string) {
	fs := flag.NewFlagSet("remove", flag.ExitOnError)
	dryRun := fs.Bool("dry-run", false, "Show what would be done")
	repository := fs.String("repository", "", "Repository URL to disambiguate")

	fs.Parse(args)

	if fs.NArg() < 1 {
		fmt.Fprintln(os.Stderr, "error: path required")
		os.Exit(1)
	}

	path := fs.Arg(0)
	var targetDir string
	if fs.NArg() > 1 {
		targetDir = fs.Arg(1)
	}

	removeFile(path, targetDir, *repository, *dryRun)
}

func addFile(repository, path, commit, branch string, glob *bool, comment, targetDir string, dryRun, force bool, forceType string) {
	path = strings.TrimPrefix(path, "/")

	// Determine commit reference
	commitRef := commit
	isTrackingBranch := false

	if commit != "" {
		isTrackingBranch = false
	} else if branch != "" {
		commitRef = branch
		isTrackingBranch = true
	} else {
		// Use default branch
		defaultBranch, err := getDefaultBranch(repository)
		if err != nil {
			fmt.Fprintf(os.Stderr, "fatal: repository '%s' not found or inaccessible\n", repository)
			os.Exit(1)
		}
		commitRef = defaultBranch
		isTrackingBranch = true
		branch = defaultBranch
	}

	if dryRun {
		fmt.Printf("Would validate repository access: %s\n", repository)
		// In dry-run, validate repository access
		cmd := exec.Command("git", "ls-remote", "--heads", "--tags", repository)
		if err := cmd.Run(); err != nil {
			fmt.Fprintf(os.Stderr, "error: cannot access repository\n")
			return
		}
		fmt.Println("repository access confirmed")
	}

	config := loadRemoteFiles()

	currentDir := getRelativePathFromGitRoot()
	manifestTarget := getManifestTargetPath(targetDir, currentDir)

	// Create unique section name
	section := fmt.Sprintf(`file "%s" from "%s"`, path, repository)

	// Check for conflicts
	var conflictingSection string
	var existingEntry string
	for sec, data := range config.Sections {
		if data.Path == path && data.RepoURL == repository {
			existingEntry = sec
			if data.Target == manifestTarget {
				if !force {
					fmt.Fprintf(os.Stderr, "fatal: '%s' already tracked from %s\n", path, repository)
					fmt.Fprintln(os.Stderr, "hint: use --force to overwrite")
					os.Exit(1)
				}
				conflictingSection = sec
				break
			}
		}
	}

	// Remove conflicting section if force is used
	if conflictingSection != "" {
		delete(config.Sections, conflictingSection)
	}

	// Update existing entry if found
	if existingEntry != "" && conflictingSection == "" {
		section = existingEntry
	}

	// Resolve commit reference
	actualCommit, err := resolveCommitRef(repository, commitRef)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to resolve commit reference '%s': %v\n", commitRef, err)
		return
	}

	if dryRun {
		action := "add"
		if _, exists := config.Sections[section]; exists {
			action = "update"
		}
		patternType := "file"
		if glob != nil && *glob || (glob == nil && isGlobPattern(path)) {
			patternType = "glob pattern"
		}
		targetInfo := ""
		if targetDir != "" {
			targetInfo = fmt.Sprintf(" -> %s", targetDir)
		}

		statusMsg := ""
		if isTrackingBranch {
			shortCommit := getShortCommit(actualCommit)
			statusMsg = fmt.Sprintf("On branch %s at %s", branch, shortCommit)
		} else {
			shortCommit := getShortCommit(actualCommit)
			statusMsg = fmt.Sprintf("HEAD detached at %s", shortCommit)
		}

		fmt.Printf("Would %s %s %s%s from %s (%s)\n", action, patternType, path, targetInfo, repository, statusMsg)
		if comment != "" {
			fmt.Printf("With comment: %s\n", comment)
		}
		return
	}

	// Create config section
	if config.Sections[section] == nil {
		config.Sections[section] = &ConfigSection{}
	}

	cs := config.Sections[section]
	cs.Path = path
	cs.RepoURL = repository
	cs.Commit = actualCommit

	if isTrackingBranch {
		cs.Branch = branch
	} else {
		cs.Branch = ""
	}

	if glob != nil {
		if *glob {
			cs.Glob = "true"
		} else {
			cs.Glob = "false"
		}
	}

	if manifestTarget != "" {
		cs.Target = manifestTarget
	}

	if comment != "" {
		cs.Comment = comment
	}

	if forceType != "" {
		cs.ForceType = forceType
	}

	saveRemoteFiles(config)

	patternType := "file"
	if glob != nil && *glob || (glob == nil && isGlobPattern(path)) {
		patternType = "glob pattern"
	}
	targetInfo := ""
	if targetDir != "" {
		targetInfo = fmt.Sprintf(" -> %s", targetDir)
	}

	statusMsg := ""
	if isTrackingBranch {
		statusMsg = fmt.Sprintf("On branch %s at %s", branch, getShortCommit(actualCommit))
	} else {
		statusMsg = fmt.Sprintf("HEAD detached at %s", getShortCommit(actualCommit))
	}

	fmt.Printf("Added %s %s%s from %s (%s)\n", patternType, path, targetInfo, repository, statusMsg)
}

func pullFiles(force, dryRun bool, jobs int, commitMessage string, edit, noCommit, autoCommit bool, repo string) {
	config := loadRemoteFiles()

	if len(config.Sections) == 0 {
		if dryRun {
			fmt.Println("No remote files tracked.")
		}
		return
	}

	// Collect file entries
	var fileEntries []*ConfigSection
	for _, section := range config.Sections {
		fileEntries = append(fileEntries, section)
	}

	// Resolve branch commits
	for _, entry := range fileEntries {
		if entry.Branch != "" {
			latestCommit, err := resolveCommitRef(entry.RepoURL, entry.Branch)
			if err != nil {
				fmt.Fprintf(os.Stderr, "warning: failed to resolve branch '%s' for %s\n", entry.Branch, entry.Path)
			} else {
				entry.FetchedCommit = latestCommit
			}
		} else {
			entry.FetchedCommit = entry.Commit
		}
	}

	// Group by repository and commit
	type repoKey struct {
		repo   string
		commit string
	}
	repoGroups := make(map[repoKey][]*ConfigSection)
	for _, entry := range fileEntries {
		key := repoKey{entry.RepoURL, entry.FetchedCommit}
		repoGroups[key] = append(repoGroups[key], entry)
	}

	if dryRun {
		var wouldFetch, wouldSkip, upToDate, errors []string
		for _, entry := range fileEntries {
			targetPath, cacheKey := getTargetPathAndCacheKey(entry.Path, entry.Target, isGlobPattern(entry.Path), entry.ForceType)
			cacheFile := filepath.Join(getCacheDir(), cacheKey)
			localHash := hashFile(targetPath)
			lastHash := readCacheFile(cacheFile)

			hasLocalChanges := localHash != "" && localHash != lastHash
			commitUpdated := entry.FetchedCommit != entry.Commit

			if hasLocalChanges && !force {
				wouldSkip = append(wouldSkip, fmt.Sprintf("%s from %s", entry.Path, entry.RepoURL))
			} else if !hasLocalChanges && !commitUpdated {
				statusDisplay := ""
				if entry.Branch != "" {
					statusDisplay = fmt.Sprintf("On branch %s at %s", entry.Branch, getShortCommit(entry.FetchedCommit))
				} else {
					statusDisplay = fmt.Sprintf("HEAD detached at %s", getShortCommit(entry.Commit))
				}
				upToDate = append(upToDate, fmt.Sprintf("%s from %s (%s)", entry.Path, entry.RepoURL, statusDisplay))
			} else {
				statusInfo := ""
				if entry.Branch != "" {
					statusInfo = fmt.Sprintf("On branch %s", entry.Branch)
					if commitUpdated {
						statusInfo += " -> [update to latest]"
					}
				} else {
					statusInfo = fmt.Sprintf("HEAD detached at %s", getShortCommit(entry.Commit))
				}
				wouldFetch = append(wouldFetch, fmt.Sprintf("%s from %s (%s)", entry.Path, entry.RepoURL, statusInfo))
			}
		}

		if len(wouldFetch) > 0 {
			fmt.Println("Would fetch:")
			for _, item := range wouldFetch {
				fmt.Printf("  %s\n", item)
			}
			fmt.Println()
		}

		if len(wouldSkip) > 0 {
			fmt.Println("Would skip (local changes):")
			for _, item := range wouldSkip {
				fmt.Printf("  %s (use --force to overwrite)\n", item)
			}
			fmt.Println()
		}

		if len(upToDate) > 0 {
			fmt.Println("Up to date:")
			for _, item := range upToDate {
				fmt.Printf("  %s\n", item)
			}
			fmt.Println()
		}

		if len(errors) > 0 {
			fmt.Println("Errors:")
			for _, item := range errors {
				fmt.Printf("  %s\n", item)
			}
			fmt.Println()
		}

		if len(wouldFetch) == 0 && len(wouldSkip) == 0 && len(upToDate) == 0 && len(errors) == 0 {
			fmt.Println("Already up to date.")
		}
		return
	}

	// Determine job count
	if jobs <= 0 {
		jobs = runtime.NumCPU()
		if jobs > len(repoGroups) {
			jobs = len(repoGroups)
		}
	} else if jobs > len(repoGroups) {
		jobs = len(repoGroups)
	}
	if jobs < 1 {
		jobs = 1
	}

	// Process repository groups concurrently
	var wg sync.WaitGroup
	resultsChan := make(chan []FileResult, len(repoGroups))

	semaphore := make(chan struct{}, jobs)
	for key, entries := range repoGroups {
		wg.Add(1)
		go func(k repoKey, e []*ConfigSection) {
			defer wg.Done()
			semaphore <- struct{}{}
			defer func() { <-semaphore }()

			results := fetchRepositoryGroup(k.repo, k.commit, e, force)
			resultsChan <- results
		}(key, entries)
	}

	go func() {
		wg.Wait()
		close(resultsChan)
	}()

	// Collect results
	var allResults []FileResult
	for results := range resultsChan {
		allResults = append(allResults, results...)
		for _, result := range results {
			if !result.Success {
				fmt.Fprintf(os.Stderr, "error: fetching %s: %s\n", result.Path, result.Error)
			}
		}
	}

	// Update config with new commits
	updated := false
	for _, result := range allResults {
		if result.Success && result.Commit != "HEAD" {
			if !strings.HasPrefix(result.FetchedCommit, result.Commit[:7]) {
				for _, section := range config.Sections {
					if section.Path == result.Path && section.RepoURL == result.Repository {
						section.Commit = result.FetchedCommit
						updated = true
					}
				}
			}
		}
	}

	if updated {
		saveRemoteFiles(config)
	}

	// Show status
	totalUpdated := 0
	totalUpToDate := 0
	totalErrors := 0
	for _, result := range allResults {
		if result.Success {
			totalUpdated += result.FilesUpdated
			totalUpToDate += result.FilesUpToDate
		} else {
			totalErrors++
		}
	}

	if totalUpdated == 0 && totalErrors == 0 && !updated {
		if totalUpToDate > 0 {
			fmt.Println("Already up to date.")
		}
	}

	// Auto-commit if requested
	shouldCommit := !noCommit && (commitMessage != "" || edit || autoCommit)
	if shouldCommit {
		commitChanges(commitMessage, edit, allResults)
	}
}

func removeFile(path, targetDir, repository string, dryRun bool) {
	path = strings.TrimPrefix(path, "/")

	config := loadRemoteFiles()

	var matchingSections []string
	for section, data := range config.Sections {
		if data.Path == path {
			if targetDir != "" && data.Target != targetDir {
				continue
			}
			if repository != "" && data.RepoURL != repository {
				continue
			}
			matchingSections = append(matchingSections, section)
		}
	}

	if len(matchingSections) == 0 {
		errorMsg := fmt.Sprintf("error: file '%s'", path)
		if targetDir != "" {
			errorMsg += fmt.Sprintf(" with target '%s'", targetDir)
		}
		if repository != "" {
			errorMsg += fmt.Sprintf(" from repository '%s'", repository)
		}
		errorMsg += " is not currently tracked"
		fmt.Println(errorMsg)
		return
	}

	if len(matchingSections) == 1 {
		section := matchingSections[0]
		data := config.Sections[section]
		targetInfo := ""
		if data.Target != "" {
			targetInfo = fmt.Sprintf(" -> %s", data.Target)
		}

		if dryRun {
			fmt.Printf("Would remove tracking for '%s%s' from %s\n", path, targetInfo, data.RepoURL)
			return
		}

		delete(config.Sections, section)
		saveRemoteFiles(config)
		fmt.Printf("Removed tracking for '%s%s' from %s\n", path, targetInfo, data.RepoURL)
		return
	}

	// Multiple sections found
	fmt.Fprintf(os.Stderr, "error: multiple entries found for '%s'. Please specify which one to remove:\n", path)
	for i, section := range matchingSections {
		data := config.Sections[section]
		targetInfo := ""
		if data.Target != "" {
			targetInfo = fmt.Sprintf(" -> %s", data.Target)
		}
		fmt.Fprintf(os.Stderr, "  %d. %s%s from %s\n", i+1, path, targetInfo, data.RepoURL)
	}
}

func statusFiles() {
	config := loadRemoteFiles()

	if len(config.Sections) == 0 {
		fmt.Println("No remote files tracked.")
		return
	}

	// Sort sections for consistent output
	var sections []string
	for section := range config.Sections {
		sections = append(sections, section)
	}
	sort.Strings(sections)

	for _, section := range sections {
		data := config.Sections[section]

		pathDisplay := data.Path
		if data.Target != "" {
			pathDisplay += fmt.Sprintf(" -> %s", data.Target)
		}

		globIndicator := ""
		if data.Glob == "true" || (data.Glob == "" && isGlobPattern(data.Path)) {
			globIndicator = " (glob)"
		}

		statusDisplay := ""
		if data.Branch != "" {
			statusDisplay = fmt.Sprintf("On branch %s at %s", data.Branch, getShortCommit(data.Commit))
		} else {
			statusDisplay = fmt.Sprintf("HEAD detached at %s", getShortCommit(data.Commit))
		}

		line := fmt.Sprintf("%s%s\t%s (%s)", pathDisplay, globIndicator, data.RepoURL, statusDisplay)

		if data.Comment != "" {
			line += fmt.Sprintf(" # %s", data.Comment)
		}

		fmt.Println(line)
	}
}

func fetchRepositoryGroup(repository, commit string, entries []*ConfigSection, force bool) []FileResult {
	var results []FileResult

	tempDir := getTempDir()
	os.MkdirAll(tempDir, 0755)

	cloneDir, err := os.MkdirTemp(tempDir, "clone-*")
	if err != nil {
		for _, entry := range entries {
			results = append(results, FileResult{
				Path:       entry.Path,
				Repository: entry.RepoURL,
				Commit:     entry.Commit,
				Branch:     entry.Branch,
				Success:    false,
				Error:      err.Error(),
			})
		}
		return results
	}
	defer os.RemoveAll(cloneDir)

	fetchedCommit, err := cloneRepositoryAtCommit(repository, commit, cloneDir)
	if err != nil {
		for _, entry := range entries {
			results = append(results, FileResult{
				Path:       entry.Path,
				Repository: entry.RepoURL,
				Commit:     entry.Commit,
				Branch:     entry.Branch,
				Success:    false,
				Error:      err.Error(),
			})
		}
		return results
	}

	for _, entry := range entries {
		isGlob := entry.Glob == "true" || (entry.Glob == "" && isGlobPattern(entry.Path))
		files := []string{entry.Path}

		if isGlob {
			files, err = getFilesFromGlob(cloneDir, entry.Path, repository)
			if err != nil {
				results = append(results, FileResult{
					Path:       entry.Path,
					Repository: entry.RepoURL,
					Commit:     entry.Commit,
					Branch:     entry.Branch,
					Success:    false,
					Error:      err.Error(),
				})
				continue
			}
		}

		filesProcessed := 0
		filesUpdated := 0
		filesUpToDate := 0
		filesSkipped := 0

		for _, f := range files {
			targetPath, cacheKey := getTargetPathAndCacheKey(f, entry.Target, isGlob, entry.ForceType)
			cacheFile := filepath.Join(getCacheDir(), cacheKey)
			sourceFile := filepath.Join(cloneDir, f)

			result := processFileCopy(sourceFile, targetPath, cacheFile, force, f, commit, entry.FetchedCommit != entry.Commit)
			filesProcessed++
			switch result {
			case "updated":
				filesUpdated++
			case "up_to_date":
				filesUpToDate++
			case "skipped":
				filesSkipped++
			}
		}

		results = append(results, FileResult{
			Path:           entry.Path,
			Repository:     entry.RepoURL,
			Commit:         entry.Commit,
			Branch:         entry.Branch,
			FetchedCommit:  fetchedCommit,
			FilesProcessed: filesProcessed,
			FilesUpdated:   filesUpdated,
			FilesUpToDate:  filesUpToDate,
			FilesSkipped:   filesSkipped,
			Success:        true,
		})
	}

	return results
}

func loadRemoteFiles() *Config {
	config := &Config{Sections: make(map[string]*ConfigSection)}

	gitRoot := getGitRoot()
	manifestPath := filepath.Join(gitRoot, remoteFileManifest)

	file, err := os.Open(manifestPath)
	if err != nil {
		return config
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	var currentSection string
	var currentData *ConfigSection

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())

		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			// Save previous section
			if currentSection != "" && currentData != nil {
				config.Sections[currentSection] = currentData
			}

			// Parse new section
			currentSection = strings.TrimSpace(line[1 : len(line)-1])
			currentData = &ConfigSection{}

			// Extract path and repo from section
			if strings.Contains(currentSection, `" from "`) {
				parts := strings.Split(currentSection, `" from "`)
				if len(parts) == 2 {
					currentData.Path = strings.TrimPrefix(parts[0], `file "`)
					currentData.RepoURL = strings.TrimSuffix(parts[1], `"`)
				}
			} else if strings.HasPrefix(currentSection, "file ") {
				currentData.Path = strings.Trim(strings.TrimPrefix(currentSection, "file "), `"`)
			}
			continue
		}

		if currentData != nil && strings.Contains(line, "=") {
			parts := strings.SplitN(line, "=", 2)
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])

			switch key {
			case "commit":
				currentData.Commit = value
			case "branch":
				currentData.Branch = value
			case "target":
				currentData.Target = value
			case "comment":
				currentData.Comment = value
			case "glob":
				currentData.Glob = value
			case "force_type":
				currentData.ForceType = value
			case "repository", "repo":
				if currentData.RepoURL == "" {
					currentData.RepoURL = value
				}
			}
		}
	}

	// Save last section
	if currentSection != "" && currentData != nil {
		config.Sections[currentSection] = currentData
	}

	return config
}

func saveRemoteFiles(config *Config) {
	gitRoot := getGitRoot()
	manifestPath := filepath.Join(gitRoot, remoteFileManifest)

	file, err := os.Create(manifestPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: failed to save manifest: %v\n", err)
		return
	}
	defer file.Close()

	writer := bufio.NewWriter(file)

	// Sort sections for consistent output
	var sections []string
	for section := range config.Sections {
		sections = append(sections, section)
	}
	sort.Strings(sections)

	for _, section := range sections {
		data := config.Sections[section]

		fmt.Fprintf(writer, "[%s]\n", section)
		fmt.Fprintf(writer, "commit = %s\n", data.Commit)

		if data.Branch != "" {
			fmt.Fprintf(writer, "branch = %s\n", data.Branch)
		}
		if data.Target != "" {
			fmt.Fprintf(writer, "target = %s\n", data.Target)
		}
		if data.Comment != "" {
			fmt.Fprintf(writer, "comment = %s\n", data.Comment)
		}
		if data.Glob != "" {
			fmt.Fprintf(writer, "glob = %s\n", data.Glob)
		}
		if data.ForceType != "" {
			fmt.Fprintf(writer, "force_type = %s\n", data.ForceType)
		}

		fmt.Fprintln(writer)
	}

	writer.Flush()
}

func hashFile(path string) string {
	file, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer file.Close()

	hash := sha1.New()
	if _, err := io.Copy(hash, file); err != nil {
		return ""
	}

	return hex.EncodeToString(hash.Sum(nil))
}

func readCacheFile(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func getShortCommit(commit string) string {
	if len(commit) > 7 {
		return commit[:7]
	}
	return commit
}

func resolveCommitRef(repository, commitRef string) (string, error) {
	cmd := exec.Command("git", "ls-remote", repository, commitRef)
	output, err := cmd.Output()
	if err != nil {
		// Try HEAD
		cmd = exec.Command("git", "ls-remote", repository, "HEAD")
		output, err = cmd.Output()
		if err != nil {
			return "", err
		}
	}

	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	if len(lines) > 0 && len(lines[0]) > 0 {
		parts := strings.Fields(lines[0])
		if len(parts) > 0 {
			return parts[0], nil
		}
	}

	return "", fmt.Errorf("failed to resolve commit reference")
}

func getDefaultBranch(repository string) (string, error) {
	cmd := exec.Command("git", "ls-remote", "--symref", repository, "HEAD")
	output, err := cmd.Output()
	if err != nil {
		return "", err
	}

	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for _, line := range lines {
		if strings.HasPrefix(line, "ref: refs/heads/") {
			parts := strings.Split(line, "refs/heads/")
			if len(parts) == 2 {
				return strings.Fields(parts[1])[0], nil
			}
		}
	}

	// Fallback
	cmd = exec.Command("git", "ls-remote", "--heads", repository)
	output, err = cmd.Output()
	if err != nil {
		return "master", nil
	}

	if strings.Contains(string(output), "refs/heads/main") {
		return "main", nil
	}
	if strings.Contains(string(output), "refs/heads/master") {
		return "master", nil
	}

	return "master", nil
}

func isGlobPattern(path string) bool {
	return strings.ContainsAny(path, "*?[{")
}

func getTargetPathAndCacheKey(path, targetDir string, isGlob bool, forceType string) (string, string) {
	relativePath := strings.TrimPrefix(path, "/")
	gitRoot := getGitRoot()

	var targetPath string
	var cacheKey string

	if targetDir != "" {
		if isGlob {
			targetPath = filepath.Join(targetDir, relativePath)
			cacheKey = strings.ReplaceAll(fmt.Sprintf("%s_%s", targetDir, relativePath), "/", "_")
		} else {
			target := targetDir
			isDirectory := false

			if strings.HasSuffix(targetDir, "/") {
				isDirectory = true
			} else if forceType == "directory" {
				isDirectory = true
			} else if forceType == "file" {
				isDirectory = false
			} else {
				hasExtension := filepath.Ext(target) != ""
				isDotfile := strings.HasPrefix(filepath.Base(target), ".") && len(filepath.Base(target)) > 1
				isDirectory = !hasExtension && !isDotfile
			}

			if isDirectory {
				filename := filepath.Base(relativePath)
				targetPath = filepath.Join(target, filename)
				cacheKey = strings.ReplaceAll(fmt.Sprintf("%s_%s", targetDir, filename), "/", "_")
			} else {
				targetPath = target
				cacheKey = strings.ReplaceAll(target, "/", "_")
			}
		}
	} else {
		targetPath = relativePath
		cacheKey = strings.ReplaceAll(relativePath, "/", "_")
	}

	// Make targetPath absolute by resolving it relative to git root
	if !filepath.IsAbs(targetPath) {
		targetPath = filepath.Join(gitRoot, targetPath)
	}

	return targetPath, cacheKey
}

func getGitRoot() string {
	cmd := exec.Command("git", "rev-parse", "--show-toplevel")
	output, err := cmd.Output()
	if err != nil {
		return "."
	}
	return strings.TrimSpace(string(output))
}

func getCacheDir() string {
	gitRoot := getGitRoot()
	return filepath.Join(gitRoot, cacheDir)
}

func getTempDir() string {
	gitRoot := getGitRoot()
	return filepath.Join(gitRoot, tempDir)
}

func getManifestTargetPath(targetDir, currentDir string) string {
	if targetDir == "" {
		if currentDir == "." {
			return ""
		}
		return currentDir
	}

	if filepath.IsAbs(targetDir) {
		return targetDir
	}

	if currentDir == "." {
		return targetDir
	}

	return filepath.Join(currentDir, targetDir)
}

func getRelativePathFromGitRoot() string {
	gitPrefix := os.Getenv("GIT_PREFIX")
	if gitPrefix != "" {
		return strings.TrimSuffix(gitPrefix, "/")
	}
	return "."
}

func processFileCopy(sourceFile, targetPath, cacheFile string, force bool, filePath, commit string, isBranchUpdate bool) string {
	localHash := hashFile(targetPath)
	lastHash := readCacheFile(cacheFile)

	hasLocalChanges := localHash != "" && localHash != lastHash

	if _, err := os.Stat(sourceFile); err == nil {
		sourceHash := hashFile(sourceFile)
		if localHash == sourceHash {
			// Update cache
			os.MkdirAll(filepath.Dir(cacheFile), 0755)
			os.WriteFile(cacheFile, []byte(sourceHash), 0644)
			return "up_to_date"
		}

		if hasLocalChanges && !force && !isBranchUpdate {
			fmt.Printf("Skipping %s: local changes detected. Use --force to overwrite.\n", strings.TrimPrefix(filePath, "/"))
			return "skipped"
		}

		os.MkdirAll(filepath.Dir(targetPath), 0755)
		input, err := os.ReadFile(sourceFile)
		if err != nil {
			return "skipped"
		}

		err = os.WriteFile(targetPath, input, 0644)
		if err != nil {
			return "skipped"
		}

		newHash := hashFile(targetPath)
		os.MkdirAll(filepath.Dir(cacheFile), 0755)
		os.WriteFile(cacheFile, []byte(newHash), 0644)

		fmt.Printf("Fetched %s -> %s at %s\n", strings.TrimPrefix(filePath, "/"), targetPath, commit)
		return "updated"
	}

	fmt.Printf("warning: file %s not found in repository\n", filePath)
	return "skipped"
}

func cloneRepositoryAtCommit(repository, commit, cloneDir string) (string, error) {
	if commit == "HEAD" || commit == "" {
		cmd := exec.Command("git", "clone", "--depth", "1", repository, cloneDir)
		if err := cmd.Run(); err != nil {
			return "", err
		}
	} else {
		isCommitHash := len(commit) == 40
		if isCommitHash {
			allHex := true
			for _, c := range strings.ToLower(commit) {
				if !strings.ContainsRune("0123456789abcdef", c) {
					allHex = false
					break
				}
			}
			if allHex {
				cmd := exec.Command("git", "clone", repository, cloneDir)
				if err := cmd.Run(); err != nil {
					return "", err
				}
				cmd = exec.Command("git", "checkout", commit)
				cmd.Dir = cloneDir
				if err := cmd.Run(); err != nil {
					return "", err
				}
			} else {
				cmd := exec.Command("git", "clone", "--depth", "1", "--branch", commit, repository, cloneDir)
				if err := cmd.Run(); err != nil {
					return "", err
				}
			}
		} else {
			cmd := exec.Command("git", "clone", "--depth", "1", "--branch", commit, repository, cloneDir)
			if err := cmd.Run(); err != nil {
				return "", err
			}
		}
	}

	cmd := exec.Command("git", "rev-parse", "HEAD")
	cmd.Dir = cloneDir
	output, err := cmd.Output()
	if err != nil {
		return "", err
	}

	return strings.TrimSpace(string(output)), nil
}

func getFilesFromGlob(cloneDir, pattern, repository string) ([]string, error) {
	cmd := exec.Command("git", "ls-tree", "-r", "--name-only", "HEAD")
	cmd.Dir = cloneDir
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	var files []string
	lines := strings.Split(strings.TrimSpace(string(output)), "\n")
	for _, line := range lines {
		matched, _ := filepath.Match(pattern, line)
		if matched {
			files = append(files, line)
		}
	}

	if len(files) > 0 {
		fmt.Printf("Found %d files matching '%s' in %s\n", len(files), pattern, repository)
	} else {
		fmt.Printf("No files found matching '%s' in %s\n", pattern, repository)
	}

	return files, nil
}

func commitChanges(commitMessage string, edit bool, fileResults []FileResult) {
	cmd := exec.Command("git", "rev-parse", "--git-dir")
	if err := cmd.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "warning: not in a git repository, skipping commit")
		return
	}

	cmd = exec.Command("git", "add", ".")
	if err := cmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "warning: failed to stage changes: %v\n", err)
		return
	}

	if commitMessage == "" {
		commitMessage = generateDefaultCommitMessage(fileResults)
	}

	var commitCmd *exec.Cmd
	if edit {
		commitCmd = exec.Command("git", "commit")
		commitCmd.Stdin = os.Stdin
		commitCmd.Stdout = os.Stdout
		commitCmd.Stderr = os.Stderr
	} else {
		commitCmd = exec.Command("git", "commit", "-m", commitMessage)
	}

	if err := commitCmd.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "warning: failed to commit changes: %v\n", err)
		return
	}

	if edit {
		fmt.Println("Committed changes: [via editor]")
	} else {
		fmt.Printf("Committed changes: %s\n", commitMessage)
	}
}

func generateDefaultCommitMessage(fileResults []FileResult) string {
	if len(fileResults) == 0 {
		return "Update remote files"
	}

	var successful []FileResult
	for _, r := range fileResults {
		if r.Success {
			successful = append(successful, r)
		}
	}

	if len(successful) == 0 {
		return "Update remote files"
	}

	if len(successful) == 1 {
		result := successful[0]
		fileName := filepath.Base(result.Path)
		repoName := extractRepoName(result.Repository)

		if result.Branch != "" {
			return fmt.Sprintf("Update %s from %s#%s", fileName, repoName, result.Branch)
		} else if len(result.FetchedCommit) >= 7 {
			return fmt.Sprintf("Update %s from %s@%s", fileName, repoName, result.FetchedCommit[:7])
		}
		return fmt.Sprintf("Update %s from %s", fileName, repoName)
	}

	return fmt.Sprintf("Update %d files", len(successful))
}

func extractRepoName(repoURL string) string {
	// Extract from GitHub/GitLab/Bitbucket URLs
	re := regexp.MustCompile(`([^/:]+)/([^/]+?)(?:\.git)?$`)
	matches := re.FindStringSubmatch(repoURL)
	if len(matches) >= 3 {
		return matches[1] + "/" + matches[2]
	}

	// Fallback: just use the last component
	parts := strings.Split(strings.TrimSuffix(repoURL, ".git"), "/")
	if len(parts) > 0 {
		return parts[len(parts)-1]
	}

	return "unknown"
}
