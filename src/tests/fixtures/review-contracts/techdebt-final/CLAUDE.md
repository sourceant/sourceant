# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm test                    # Run all tests
npm test -- --testPathPatterns=src/analyzers  # Run a specific test suite
npm run build               # Compile TypeScript
npm run dev                 # Run with ts-node (no build needed)
npm run watch               # Compile in watch mode
npm run lint                # Lint source files
```

**Before every commit:** run `npm test` then `npm run build`. Both must succeed.

## Architecture

Tech Debt MCP is a Model Context Protocol (MCP) server exposing tools and resources for static tech-debt analysis across 14 languages.

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

### Request flow

**Tools:** `MCP client` → `handlers.ts` (`CallToolRequestSchema` switch) → `AnalysisEngine` → `createAnalyzer()` (per file language) → `[Language]Analyzer.performLanguageSpecificChecks()` → issues array (inline suppression applied per-line in `checkPattern`/`checkTodoComments`) → `applyRuleExclusions()` (filter by config globs) → `formatters.ts` → response.

**Resources:** `MCP client` → `resourceHandlers.ts` (via `McpServer.registerResource()`) → `AnalysisEngine.analyzeProject()` → JSON response.

### Key conventions

- **Imports use `.js` extensions** — required for `module: NodeNext` / `moduleResolution: NodeNext` in tsconfig.
- **All types in `src/types/index.ts`** — never define types locally.
- **Factory pattern** — use `createAnalyzer(language, config)` and `createDependencyParser(filePath)`.
- **BaseAnalyzer.checkPattern()** — the standard way to match regex patterns and emit `TechDebtIssue` objects.
- **Domain handler extraction** — when handlers.ts grows, extract domain-specific handlers to dedicated files (e.g., `configValidator.ts`, `dependencyHandlers.ts`, `resourceHandlers.ts`).
- **No `any`** — use `unknown` if truly needed; no `@ts-ignore` (use `@ts-expect-error` with a comment).
- **No `console.log`** in production code.
- **JSDoc on all public functions.**

## Using checkPattern

```typescript
// In performLanguageSpecificChecks():
issues.push(...this.checkPattern(filePath, content, /pattern/g, {
  category: 'code-quality',
  severity: 'medium',
  title: 'Issue title',
  description: 'What the issue is',
  suggestion: 'How to fix it',
  effort: 'small',
  rule: 'rule-name',
  tags: ['tag1'],
}));
```

## Inline Suppression

Use `// techdebt-ignore-next-line [rule]` or block comments to suppress false positives directly in source code. Both `checkPattern()` and `checkTodoComments()` respect these directives.

```typescript
// Suppress the next line (all rules):
// techdebt-ignore-next-line
debugger;

// Suppress the next line (specific rule only):
// techdebt-ignore-next-line debugger
debugger;

// Suppress a block of lines:
// techdebt-ignore-start ts-ignore
issues.push(...this.checkPattern(filePath, content, /@ts-ignore/g, {
  title: '@ts-ignore comment found',
  // ...
}));
// techdebt-ignore-end ts-ignore
```

Use this to prevent self-detection false positives in analyzer source files — wrap pattern definitions in rule-specific blocks (e.g., `// techdebt-ignore-start debugger`...`// techdebt-ignore-end debugger`).

## Enums Reference

**Severity:** `critical` | `high` | `medium` | `low`

**Effort:** `trivial` (<5m) | `small` (5-30m) | `medium` (30m-2h) | `large` (2-4h) | `xlarge` (4h+)

**Categories:** `dependency` | `code-quality` | `architecture` | `documentation` | `testing` | `security` | `performance` | `maintainability`

## Adding a New Language Analyzer

1. Create `src/analyzers/[language]Analyzer.ts` extending `BaseAnalyzer`.
2. Implement `performLanguageSpecificChecks(filePath, content): Promise<TechDebtIssue[]>`.
3. Register in `src/analyzers/index.ts` `createAnalyzer()` switch.
4. Add `LanguageConfig` in `src/config/languages.ts`.
5. Add the language to `SupportedLanguage` union in `src/types/index.ts`.

## Adding a New MCP Resource

1. Add a `mcpServer.registerResource()` call in `src/server/resourceHandlers.ts`.
2. Use `ResourceTemplate` with `{ list: undefined }` for non-enumerable templates.
3. RFC 6570 `{+variable}` expansion allows slashes in path variables.
4. Use `jsonSuccessResponse(uri, data)` to return results and `jsonErrorResponse(uri, message)` for validation errors. Both produce the standard `{ contents: [{ uri, mimeType, text }] }` shape.
5. Wrap the callback logic with `withResourceErrorHandling(uri, () => ...)` — it catches thrown errors and returns `jsonErrorResponse` automatically.
6. No changes needed in `setup.ts` — SDK auto-registers `resources` capability.

## Adding a New MCP Tool

1. Add the tool schema to `TOOL_DEFINITIONS` in `src/server/tools.ts`.
2. Add a `case 'tool_name':` in `handlers.ts` `CallToolRequestSchema` handler.
3. Implement the handler function — in `handlers.ts` for core tools, or in a dedicated file (e.g., `configValidator.ts`, `dependencyHandlers.ts`) for domain-specific tools. Keep `handlers.ts` under 500 lines.
4. Validate tool arguments using `inputParser.ts` helpers (`requireString`, `requireRecord`, `optionalString`, `optionalNumber`, etc.) — never access `args` properties directly.

## Adding a New Dependency Parser

1. Create `src/analyzers/dependencies/[ecosystem]Parser.ts` extending `BaseDependencyParser`.
2. Register in `src/analyzers/dependencies/index.ts` factory (`createDependencyParser`) and `getAllPackageFileNames()`.
3. Add tests in `src/analyzers/dependencies/__tests__/[ecosystem]Parser.test.ts`.

### isDev Convention

- **npm:** `devDependencies` key → `isDev: true`
- **Poetry 1.2+ groups:** only `groupName === 'dev'` → `isDev: true`. `optional = false` means required (production), NOT dev.
- **Pipfile:** `[dev-packages]` section → `isDev: true`
- **Always test `isDev` for ALL groups** — asserting `isDev: false` for non-dev groups catches logic bugs.

## Design Specs

Implementation details for planned phases are in `docs/superpowers/specs/2026-03-19-phases-3-4-6-design.md`. This includes types, API surfaces, file changes, algorithms, and tool definitions for:
- **Phase 3 (v2.1.0):** Snapshot & Trend Tracking — Issues #39-44
- **Phase 4 (v2.2.0):** Complexity Metrics — Issues #45-49

## Documentation Maintenance

See `.claude/rules/docs-maintenance.md` for the canonical list of files to update after every implementation PR. Do not defer docs to a separate PR — include them in the implementation PR.

## Git & PR Workflow

See `.claude/rules/git-workflow.md` for branching, PR, and commit conventions.

## Code Quality Limits

See `.claude/rules/code-quality.md` for file length, function length, nesting, and complexity limits. Applied automatically when editing `src/**/*.ts`.

## Testing

- Test files live in `__tests__/` folders alongside source (`[module].test.ts`).
- Follow TDD: write tests first, then implement.
- All imports in test files must include `.js` extension.
- Target >80% coverage on new code.
- Mock pattern: `jest.mock(...)` at top of file, typed references via `as jest.MockedFunction<typeof X>`.
- Jest config uses `moduleNameMapper` to strip `.js` extensions at runtime — this is why `.js` imports work in tests despite TypeScript sources.

## Security

When handling user input from MCP tool calls:

- **Path arguments:** Always validate with `path.isAbsolute()` and normalize with `path.resolve()` before any filesystem operation. Never pass user-supplied paths directly to `fs` functions. See Issues #125, #126.
- **User-supplied regex:** Never compile user-provided patterns via `new RegExp()` without length limits and flag allowlisting (`gimsuy` only). See Issue #127.
- **String interpolation into RegExp:** Always escape captured strings with a `RegExp.escape()` polyfill before interpolating into `new RegExp()`. See Issue #128.
- **Error messages:** Use `getRelativePath()` in error messages returned to clients — never leak absolute filesystem paths. See Issue #129.
