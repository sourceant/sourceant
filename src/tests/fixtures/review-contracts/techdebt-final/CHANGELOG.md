# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **README.md** — Restructured for scannability: added stat line, grouped tools table with collapsible parameter reference, Resources table, collapsed SwiftUI section, merged Custom Rules into Configuration
- **ARCHITECTURE.md** — Fixed project structure tree (added `inputParser.ts`, `argValidation.ts`, `swiftUiChecks.ts`, `swiftUiChecksPhase2.ts`), removed phantom `src/services/` directory, updated test count (522), fixed file size table, marked resolved debt items
- **CLAUDE.md** — Deduplicated Git and Code Quality sections (reference `.claude/rules/`), updated resource recipe, added Security section
- **.github/copilot-instructions.md** — Added Security Review and Testing Review sections, updated architecture tree, aligned file length threshold (500)
- **ROADMAP.md** — Added v2.0.2 security patch section with issues #124-130
- **CONTRIBUTING.md** — Updated metrics to March 2026 scan, marked resolved refactoring targets
- **GITHUB_PACKAGES.md** — Clarified npm as primary distribution, removed incorrect `require()` API example

- Reduced nesting in core engines (`analysisEngine`, `customRulesEngine`) to ≤4 levels (#118)
- Reduced nesting in server modules to ≤4 levels (#119)
- Reduced nesting depth in 10 dependency parsers (#88)
- Reduced `csharpAnalyzer.ts` nesting from 7 to ≤4 levels (#113)
- Reduced `cppParser.parseVcpkgJson` nesting from 5 to ≤4 levels via `makeVcpkgDep()` helper (#131)
- Split `swiftAnalyzer.ts` into companion modules (`swiftUiChecks.ts`, `swiftUiChecksPhase2.ts`) (#121)
- Replaced `<any>` generics and suppressed non-null-assertion false positives (#112)
- Replaced type assertions in `handlers.ts` with typed input parsers (#110)
- Replaced type assertions in `configValidator.ts` and `dependencyHandlers.ts` with type guards (#106)
- Replaced type assertions in `resourceHandlers.ts` with type guards (#107)

### Fixed
- **npm-shrinkwrap.json** — Removed to stop publishing lockfile to npm (#123)
- Replaced `console.warn` with `console.error` for MCP stdio compatibility (#100)
- Eliminated analyzer false positives via `ruleExclusions` config mechanism (#78)
- Replaced non-null assertions with destructured variables in `analysisEngine` (#101)
- Security: absolute filesystem paths no longer leaked in `scanErrors` returned to MCP clients; `findPackageFiles` now uses `getRelativePath()` for error messages (#129)
- Security: user-supplied regex patterns in `add_custom_rule` and `validate_custom_pattern` are now validated for length (≤1 000 chars) and flag allowlist (`gimsuy` only); inline `code` for `execute_custom_rules` is capped at 500 000 characters, preventing ReDoS attacks (#127)

### Added
- Direct unit tests for `swiftUiChecks` and `swiftUiChecksPhase2` modules
- Unit tests for `inputParser` module
- Inline suppression comments for tech debt issues (#105)
- Regex precision improvements to eliminate 11 analyzer false positives (#102, #104)

## [2.0.1] - 2026-03-20

### Added
- **MCP Resources (Phase 6)** — Two passive resource templates for tech debt data access:
  - `debt://summary/{+projectPath}` — Health score, SQALE metrics, issue counts by severity/category
  - `debt://issues/{+projectPath}` — Filterable issues list with severity, category, and limit query parameters
- `src/server/resourceHandlers.ts` — New module using `McpServer.registerResource()` high-level API
- 8 tests covering resource registration, reads, filters, limits, and error paths

### Changed
- **CLAUDE.md** — Consolidated as single source of truth for coding conventions; added MCP resource recipe, design spec pointer, documentation maintenance checklist
- **ARCHITECTURE.md** — Updated MCP Server section to reflect `src/server/` module split, fixed tool count (16), added `resourceHandlers.ts`, updated dependency graph
- **README.md** — Added MCP Resources section with usage documentation
- **ROADMAP.md** — Phase 6 marked complete, Phase 3/4 enriched with issue numbers, branch names, and acceptance criteria
- **.github/copilot-instructions.md** — Trimmed to Copilot-specific PR review workflow, references CLAUDE.md for conventions

## [2.0.0] - 2026-03-09

### Added
- **CODE_OF_CONDUCT.md** - Added Contributor Covenant v2.1 based Code of Conduct
  - Establishes community standards and expectations
  - Defines enforcement guidelines
  - Improves Snyk community health score
- **.techdebtrc.json** - Project-specific tech debt configuration file
  - File size limits (max 500 lines)
  - Complexity limits (max nesting depth 4)
  - Custom patterns for nullish coalescing and non-null assertions
  - Test file exclusions to prevent false positives
- **TECH_DEBT_SCAN.md** - Complete self-scan analysis with before/after comparison
  - Shows impact of .techdebtrc.json configuration
  - Documents reduction from 101 to 81 issues (-19.8%)
  - Identifies false positives vs. real technical debt
  - Provides actionable roadmap for continuous improvement
- **check_dependencies** MCP tool (Phase 2 - Dependency Analysis)
  - Scans project for package manifests across multiple ecosystems
  - Validates that the provided path is a directory (rejects file paths)
  - Returns a structured dependency report with production vs development dependencies
  - Includes failed-parse reporting and filesystem scan error surfacing
  - Filters empty manifest sections from reports (e.g., dev-only with `includeDev=false`)
  - Parsers for npm, pip, Maven/Gradle, Cargo, Go Modules, Composer, Bundler, NuGet, C/C++, and Swift Package Manager added under `src/analyzers/dependencies/`
- **validate_config** MCP tool (Phase 2 - Config Validation)
  - Validates `.techdebtrc.json` syntax and schema
  - Checks `ignore`, `include`, `rules`, `severity`, `languageOverrides`, and `customPatterns` fields
  - Guards against non-object top-level values (null, array, primitives)
  - Reuses `CustomRulesEngine.validatePattern()` for custom pattern validation
  - Returns detailed errors and warnings with actionable messages
- **get_vulnerability_report** MCP tool (Phase 2 - Offline Vulnerability Inventory)
  - Generates an offline dependency inventory for vulnerability review
  - Validates that the provided path is a directory
  - Lists all dependencies by ecosystem in tabular format
  - Filters empty manifest sections and surfaces filesystem scan errors
  - Offline-first; online CVE lookup planned for Phase 2b (OSV API)
  - Accepts `includeDev` flag (default: false) to focus on production dependencies

### Changed
- **src/index.ts refactored** — split 883-line monolith into focused modules:
  - `src/server/setup.ts` — server instantiation and transport wiring
  - `src/server/handlers.ts` — all MCP tool request handlers
  - `src/server/tools.ts` — centralized `TOOL_DEFINITIONS` array
  - `src/server/formatters.ts` — output formatting helpers
  - `src/index.ts` now an 18-line entry point only
- **README.md** - Added Code Quality section with updated SQALE rating
  - Self-scan results (A rating, 2.9% debt ratio, down from 3.4%)
  - Link to TECH_DEBT_SCAN.md with before/after comparison
  - Configuration impact metrics (81 issues vs. 101 before)
  - Link to CODE_OF_CONDUCT.md in Contributing section
  - Added SQALE rating badge to header
- **CONTRIBUTING.md** - Added Tech Debt Compliance section
  - Updated metrics (2.9% debt ratio)
  - File size and complexity limits
  - Code quality rules enforcement
  - Known refactoring targets with specific line numbers
  - Configuration impact before/after comparison
- **ARCHITECTURE.md** - Added Code Quality Standards section
  - Current project health metrics (2.9% debt ratio)
  - File size and complexity limits table
  - Known technical debt items with priorities and line numbers
  - Self-scan strategy with measured configuration impact
  - Before/after comparison showing improvement
  - Regular health checks documentation
- **.github/copilot-instructions.md** - Added Tech Debt Refactoring Rules
  - Current SQALE rating and debt ratio (2.9%)
  - File size and complexity limits
  - Refactoring priorities with specific targets
  - Code quality rules enforcement
  - Pre/post refactoring checklist

### Documentation
- Comprehensive self-scan using tech-debt-mcp tool (2 scans: with/without config)
- Measured impact of .techdebtrc.json: -20 issues (-19.8%), -10 hours (-14.3%) remediation
- Identified 1 real high-priority issue (C# analyzer nesting at line 267)
- Identified 13 false positives (analyzer pattern definitions)
- Documented specific refactoring targets (src/index.ts: 883 lines, csharpAnalyzer.ts:267)
- Established quality baselines and targets with measurable goals
- Created configuration to prevent false positives in self-scanning
- All documentation now cross-references TECH_DEBT_SCAN.md for transparency

### Metrics Summary (at time of v2.0.0 release)
- **SQALE Rating:** A ⭐⭐⭐⭐⭐ (2.9% debt ratio, improved from 3.4%)
- **Total Issues:** 81 (down from 101)
- **Remediation Time:** 60 hours (down from 70 hours)
- **Improvement:** -20 false positives, -10 hours remediation time
- **Files Analyzed:** 25 (down from 33, test files excluded)
- **Critical Issues:** 0
- **High Issues:** 14 (13 are false positives in analyzer patterns)

### Community
- Improved Snyk package health score with Code of Conduct
- Better contributor onboarding with comprehensive guidelines
- Transparent quality metrics showing "we practice what we preach"

## [1.1.0] - 2026-02-07

### Added

#### SwiftUI-Specific Technical Debt Analysis (Issue #58)
- **14 comprehensive SwiftUI checks** across 2 phases
- **Phase 1 - Core SwiftUI Checks (9 checks):**
  - Excessive @State variables detection (>5 per view)
  - @ObservedObject initialization misuse detection
  - @Environment value force unwrap detection
  - Combine pipeline circular reference detection ([weak self] validation)
  - Missing Timer cleanup in onDisappear
  - Missing Task cancellation in async operations
  - UI updates on background threads detection
  - Dynamic list missing .id() modifiers
  - Expensive calculations in view body

- **Phase 2 - Advanced SwiftUI Patterns (5 checks):**
  - AnyView type erasure detection
  - Deprecated NavigationLink patterns
  - GeometryReader root-level misuse
  - Retain cycles in SwiftUI closures
  - Deep view nesting detection (>6 levels)

#### GitHub Packages Support
- Published to both npm Registry and GitHub Packages
- Comprehensive installation documentation (GITHUB_PACKAGES.md)
- Automated CI/CD publishing workflow

#### Quality Improvements
- 96 tests passing (100% of SwiftUI implementation)
- 22 todo tests for Phase 3 enhancements
- Performance optimization: content split once per file analysis
- Issue IDs include filePath for global uniqueness
- All Copilot review suggestions addressed

### Changed
- Updated README.md with multiple installation options (npm, GitHub Packages, source)
- Enhanced publish.yml workflow to publish to both registries
- Improved SwiftUI analyzer with per-view @State counting
- Better Environment validation with force unwrap detection at usage sites

### Fixed
- Timer cleanup detection bug (lines.indexOf issue)
- Environment validation now detects actual usage patterns
- Task cancellation guidance clarified (@MainActor ≠ cancellation)
- Removed duplicate method implementations in SwiftAnalyzer
- **execute_custom_rules schema** - Removed unsupported anyOf constraint (Thanks @ophirbucai - PR #63)

### Documentation
- Added GITHUB_PACKAGES.md - Comprehensive installation and setup guide
- Updated .github/copilot-instructions.md with strict documentation requirements
- Enhanced PR workflow with mandatory documentation checklist
- **IDE Installation Badges** - One-click install for VS Code, Cursor, Claude, Windsurf, JetBrains, Xcode (Thanks @ophirbucai - PR #66)
- **Documentation Accuracy** - Fixed tool count (13 tools), test count (96 passing + 22 todo), and branch references throughout all docs

## [1.0.0] - 2026-02-07

### Added

#### Phase 0: Language Support
- Support for 14 programming languages: JavaScript, TypeScript, Python, Java, Swift, Kotlin, Objective-C, C++, C, C#, Go, Rust, Ruby, PHP
- BaseAnalyzer with factory pattern for extensibility
- Language-specific tech debt detection patterns
- Comprehensive test coverage for all analyzers

#### Phase 1: SQALE Metrics ✅
- SQALE Engine with A-E rating system
- Remediation time calculations
- Debt ratio metrics (percentage of development time)
- Category and severity breakdowns
- Human-readable time formatting
- **NEW:** `get_sqale_metrics` MCP tool for dedicated SQALE reporting
- **NEW:** SQALE metrics integrated into all analysis reports

#### Phase 5: Custom Rules
- CustomRulesEngine for pattern-based checks
- 5 MCP tools for rule management:
  - `add_custom_rule` - Add custom pattern rules
  - `remove_custom_rule` - Remove rules by ID
  - `list_custom_rules` - Show all active rules
  - `execute_custom_rules` - Run rules against code
  - `validate_custom_pattern` - Validate patterns
- Regex support with configurable flags
- Language-specific rule filtering
- Multiple matches per line
- Cross-platform line ending support (\r\n and \n)

#### MCP Tools (13 total)
1. `analyze_project` - Full project technical debt analysis
2. `analyze_file` - Single file analysis
3. `get_debt_summary` - Quick debt summary
4. `get_sqale_metrics` - SQALE metrics with rating and remediation time
5. `list_supported_languages` - Show supported languages
6. `get_recommendations` - Prioritized suggestions
7. `get_issues_by_severity` - Filter issues by severity
8. `get_issues_by_category` - Filter issues by category
9. `add_custom_rule` - Add custom pattern rules
10. `remove_custom_rule` - Remove custom rules
11. `list_custom_rules` - List all custom rules
12. `execute_custom_rules` - Execute custom rules
13. `validate_custom_pattern` - Validate custom patterns

### Documentation
- Comprehensive README with installation and usage instructions
- ARCHITECTURE.md describing system design and patterns
- CONTRIBUTING.md with contribution guidelines
- AI agent instructions for Copilot
- Full API documentation for all MCP tools

### Testing
- 96 comprehensive tests across 8 test suites (22 todo for future enhancements)
- 100% test pass rate
- Tests for all language analyzers
- Tests for SQALE engine
- Tests for custom rules engine

[Unreleased]: https://github.com/PierreJanineh/TechDebtMCP/compare/v2.0.1...HEAD
[2.0.1]: https://github.com/PierreJanineh/TechDebtMCP/releases/tag/v2.0.1
[2.0.0]: https://github.com/PierreJanineh/TechDebtMCP/releases/tag/v2.0.0
[1.1.0]: https://github.com/PierreJanineh/TechDebtMCP/releases/tag/v1.1.0
[1.0.0]: https://github.com/PierreJanineh/TechDebtMCP/releases/tag/v1.0.0

