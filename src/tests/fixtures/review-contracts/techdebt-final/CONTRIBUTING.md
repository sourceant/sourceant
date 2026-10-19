# Contributing to Tech Debt MCP

Thank you for your interest in contributing to **Tech Debt MCP** (repository: `TechDebtMCP`)! This document provides guidelines for contributing to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [How to Contribute](#how-to-contribute)
- [Coding Guidelines](#coding-guidelines)
- [Commit Messages](#commit-messages)
- [Pull Request Process](#pull-request-process)
- [Testing](#testing)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

Please be respectful and constructive in all interactions. We aim to maintain a welcoming and inclusive community.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/TechDebtMCP.git`
3. Add upstream remote: `git remote add upstream https://github.com/PierreJanineh/TechDebtMCP.git`

## Development Setup

### Prerequisites

- Node.js 18+ and npm
- Git

### Installation

```bash
# Install dependencies
npm install

# Build the project
npm run build

# Run in development mode
npm run dev

# Watch mode for development
npm run watch
```

### Project Structure

```
src/
├── index.ts                    # Entry point — creates server, attaches handlers + resources, runs
├── server/
│   ├── setup.ts                # McpServer instantiation and transport wiring
│   ├── handlers.ts             # Core MCP tool request handlers (CallToolRequestSchema)
│   ├── tools.ts                # TOOL_DEFINITIONS array (tool schemas/descriptions)
│   ├── inputParser.ts          # Tool argument validation (requireString, optionalString, etc.)
│   ├── argValidation.ts        # Argument coercion and constraint checks
│   ├── resourceHandlers.ts     # MCP resource templates (debt://summary, debt://issues)
│   ├── formatters.ts           # Output formatting helpers
│   ├── configValidator.ts      # .techdebtrc.json validation handler
│   └── dependencyHandlers.ts   # Dependency analysis & vulnerability report handlers
├── types/index.ts              # Single source of truth for all TypeScript interfaces
├── config/languages.ts         # Per-language config: extensions, package files, patterns
├── core/
│   ├── analysisEngine.ts       # Project-level orchestrator: discovers files, fans out to analyzers
│   ├── sqaleEngine.ts          # SQALE rating/debt ratio calculation
│   └── customRulesEngine.ts    # User-defined pattern rules (.techdebtrc.json)
├── analyzers/
│   ├── baseAnalyzer.ts         # Abstract base — shared logic, checkPattern(), applyRuleExclusions()
│   ├── index.ts                # createAnalyzer() factory
│   ├── [language]Analyzer.ts   # 14 language-specific analyzers
│   ├── swiftUiChecks.ts        # SwiftUI Phase 1 checks (companion to swiftAnalyzer)
│   ├── swiftUiChecksPhase2.ts  # SwiftUI Phase 2 checks (advanced patterns)
│   └── dependencies/
│       ├── baseParser.ts       # Abstract dependency parser
│       ├── index.ts            # createDependencyParser() factory
│       └── [ecosystem]Parser.ts # npm, pip, cargo, gradle, nuget, go.mod, etc.
└── utils/fileUtils.ts          # fs helpers (readFile, fileExists, getRelativePath)
```

## How to Contribute

### Types of Contributions

- **Bug fixes** — Fix issues reported in GitHub Issues
- **New features** — Add new language analyzers, MCP tools, or analysis capabilities
- **Documentation** — Improve README, guides, or code comments
- **Tests** — Add or improve test coverage

### Workflow

1. **Find or create an issue** — Check if an issue exists for what you want to work on
2. **Comment on the issue** — Let others know you're working on it
3. **Create a branch** — Use the naming convention below
4. **Make your changes** — Follow the coding guidelines
5. **Test your changes** — Ensure all tests pass
6. **Submit a pull request** — Follow the PR template

## Coding Guidelines

### TypeScript Standards

- **Strict mode enabled** — No implicit any types
- **Use async/await** — No callbacks
- **JSDoc comments required** — Document all public functions
- **No `any` types** — Use `unknown` if type is truly unknown
- **Prefer `const`** — Use `let` only when reassignment is needed
- **ES modules** — Use `.js` extensions in imports

### Tech Debt Compliance (SQALE Rating: A ⭐⭐⭐⭐⭐)

**Project maintains 4.6% debt ratio - follow these rules to keep it excellent:**

> **📊 See [TECH_DEBT_SCAN.md](TECH_DEBT_SCAN.md)** for complete self-scan results showing how `.techdebtrc.json` reduced false positives by 20 issues (-19.8%).

#### File Size & Complexity Limits
- **Max file length:** 500 lines (split larger files into modules)
- **Max nesting depth:** 4 levels (use early returns and helper functions)
- **Max function length:** 50 lines
- **Max cyclomatic complexity:** 10

#### Code Quality Rules (Enforced)
- ❌ **NO `debugger` statements** in production code
- ❌ **NO `@ts-ignore`** - Use `@ts-expect-error` with explanation
- ❌ **NO `console.log`** in production code - Remove or use proper logging
- ✅ **USE early returns** to reduce nesting
- ✅ **EXTRACT complex logic** into well-named helper functions
- ✅ **REPLACE `!` operator** with optional chaining (`?.`) or null checks
- ✅ **ADD JSDoc** to all public APIs

#### Known Refactoring Targets (Don't make worse)
1. ~~`src/index.ts` - 883 lines~~ ✅ Resolved in v2.0.0 (split into `src/server/` modules)
2. ~~`src/analyzers/csharpAnalyzer.ts:267` - Deep nesting (14 levels)~~ ✅ Resolved in PR #113 (refactored to ≤4 levels)
3. ~~Non-null assertions at `src/index.ts:804, 809`~~ ✅ Resolved in v2.0.0

#### Configuration Impact
**Current (March 2026):** 118 issues, ~96 hours (wider scan scope in v2.0.1)
**Original baseline (Feb 2026):** 101 issues, 70 hours → 81 issues, 60 hours after `.techdebtrc.json`

**Configuration:** See `.techdebtrc.json` for project-specific rules.

### Code Style

```typescript
/**
 * Example function with proper documentation
 * @param filePath Absolute path to the file
 * @param content File content to analyze
 * @returns Object containing analysis result with issues array
 */
async function analyzeCode(filePath: string, content: string): Promise<{ issues: TechDebtIssue[] }> {
  const issues: TechDebtIssue[] = [];
  // Implementation
  return { issues };
}
```

### Adding a New Language Analyzer

See [`.github/copilot-instructions.md`](.github/copilot-instructions.md) for detailed instructions on:
- Creating a new language analyzer
- Adding MCP tools
- Using the BaseAnalyzer helpers

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <description>

[optional body]

[optional footer]
```

### Types

- `feat:` — New feature
- `fix:` — Bug fix
- `docs:` — Documentation only
- `refactor:` — Code refactoring
- `test:` — Adding or updating tests
- `chore:` — Maintenance tasks (dependencies, build config, etc.)
- `perf:` — Performance improvements
- `style:` — Code style changes (formatting, semicolons, etc.)

### Examples

```
feat: add Rust language analyzer

- Implement RustAnalyzer extending BaseAnalyzer
- Add checks for unwrap(), unsafe blocks, panic!()
- Update factory to include Rust support

Closes #7
```

```
fix: correct cyclomatic complexity calculation for nested loops

The previous implementation was counting loop conditions incorrectly.
This fix ensures each loop adds exactly 1 to the complexity score.

Fixes #45
```

## Pull Request Process

### Review Process

1. **Automated checks** — CI/CD runs tests and linting
2. **Copilot review** — Review automated suggestions and address relevant ones
3. **Human review** — Maintainer reviews the code
4. **Address feedback** — Make requested changes
5. **Merge** — Squash and merge into `develop`

### Before Submitting

1. ✅ **Run tests** — `npm test` (all tests must pass)
2. ✅ **Build succeeds** — `npm run build` (no TypeScript errors)
3. ✅ **Update documentation** — If you changed functionality
4. ✅ **Add tests** — For new features or bug fixes
5. ✅ **Include docs for Phase 2 features** — If your change touches dependency parsing or adds MCP tools (e.g., `check_dependencies`), update README, ARCHITECTURE.md, and CHANGELOG.md in the same branch

### Branch Naming

- Feature: `feature/issue-{number}-short-description`
- Bug fix: `fix/issue-{number}-short-description`
- Documentation: `docs/issue-{number}-short-description`

Examples:
- `feature/issue-7-rust-analyzer`
- `fix/issue-45-complexity-calculation`
- `docs/issue-2-contributing-guide`

### PR Template

```markdown
## Description
Brief description of what this PR does.

## Changes
- List of changes made
- Another change

## Related Issue
Closes #123

## Testing
- [ ] Tests added/updated
- [ ] All tests passing
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project style guidelines
- [ ] Documentation updated
- [ ] Commit messages follow convention
- [ ] No TypeScript errors
- [ ] Copilot review suggestions addressed
```

## Testing

### Running Tests

```bash
# Run all tests
npm test

# Run tests in watch mode
npm test -- --watch

# Run specific test file
npm test -- goAnalyzer.test.ts

# Run with coverage
npm test -- --coverage
```

### Writing Tests

- Place tests in `__tests__/` folders next to source files
- Name test files: `[filename].test.ts`
- Use descriptive test names
- Jest is configured with ts-jest for TypeScript support

```typescript
describe('TypeScriptAnalyzer', () => {
  const analyzer = new TypeScriptAnalyzer();

  it('detects any type usage', async () => {
    const result = await analyzer.analyze('test.ts', 'const x: any = 5;');
    
    expect(result.issues).toContainEqual(
      expect.objectContaining({
        rule: 'any-type',
        severity: 'medium'
      })
    );
  });
});
```

## Reporting Issues

### Bug Reports

Use the bug report template and include:
- **Description** — What's the bug?
- **Steps to reproduce** — How to trigger it?
- **Expected behavior** — What should happen?
- **Actual behavior** — What actually happens?
- **Environment** — Node version, OS, etc.
- **Logs/Screenshots** — Any relevant output

### Feature Requests

Use the feature request template and include:
- **Problem** — What problem does this solve?
- **Proposed solution** — How should it work?
- **Alternatives** — Other solutions considered?
- **Additional context** — Use cases, examples, etc.

## Questions?

- Check existing [GitHub Issues](https://github.com/PierreJanineh/TechDebtMCP/issues)
- Read the [README](README.md)
- Review [`.github/copilot-instructions.md`](.github/copilot-instructions.md)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

