import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError,
} from '@modelcontextprotocol/sdk/types.js';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { AnalysisEngine } from '../core/analysisEngine.js';
import { CustomRulesEngine, MAX_FILE_SIZE_BYTES } from '../core/customRulesEngine.js';
import { analyzeFile } from '../analyzers/index.js';
import { getSupportedLanguages, LANGUAGE_CONFIGS } from '../config/languages.js';
import { readFile, fileExists, getFileStats } from '../utils/fileUtils.js';
import { formatReport, formatMinutes } from './formatters.js';
import { TOOL_DEFINITIONS } from './tools.js';
import { handleValidateConfig } from './configValidator.js';
import { handleCheckDependencies, handleGetVulnerabilityReport } from './dependencyHandlers.js';
import {
  CustomPattern,
} from '../types/index.js';
import {
  parseAnalyzeProjectInput,
  parseAnalyzeFileInput,
  parseGetDebtSummaryInput,
  parseGetSqaleMetricsInput,
  parseGetRecommendationsInput,
  parseGetIssuesBySeverityInput,
  parseGetIssuesByCategoryInput,
  parseAddCustomRuleInput,
  parseRemoveCustomRuleInput,
  parseExecuteCustomRulesInput,
  parseValidateCustomPatternInput,
} from './inputParser.js';

/** MCP tool response shape */
type ToolResponse = { content: Array<{ type: string; text: string }> };

/**
 * Dispatch a tool call by name to the appropriate handler.
 */
async function dispatchTool(
  name: string,
  args: Record<string, unknown>,
  engine: AnalysisEngine,
  customRulesEngine: CustomRulesEngine,
): Promise<ToolResponse> {
  switch (name) {
    case 'analyze_project':
      return handleAnalyzeProject(engine, args);
    case 'analyze_file':
      return handleAnalyzeFile(args);
    case 'get_debt_summary':
      return handleGetDebtSummary(engine, args);
    case 'get_sqale_metrics':
      return handleGetSqaleMetrics(engine, args);
    case 'list_supported_languages':
      return handleListSupportedLanguages();
    case 'get_recommendations':
      return handleGetRecommendations(engine, args);
    case 'get_issues_by_severity':
      return handleGetIssuesBySeverity(engine, args);
    case 'get_issues_by_category':
      return handleGetIssuesByCategory(engine, args);
    case 'add_custom_rule':
      return handleAddCustomRule(customRulesEngine, args);
    case 'remove_custom_rule':
      return handleRemoveCustomRule(customRulesEngine, args);
    case 'list_custom_rules':
      return handleListCustomRules(customRulesEngine);
    case 'execute_custom_rules':
      return handleExecuteCustomRules(customRulesEngine, args);
    case 'validate_custom_pattern':
      return handleValidateCustomPattern(args);
    case 'check_dependencies':
      return handleCheckDependencies(args);
    case 'validate_config':
      return handleValidateConfig(args);
    case 'get_vulnerability_report':
      return handleGetVulnerabilityReport(args);
    default:
      throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
  }
}

/**
 * Attach all tool handlers to a given McpServer instance.
 */
export function attachHandlers(mcpServer: McpServer): void {
  const server = mcpServer.server; // underlying low-level Server
  const engine = new AnalysisEngine();
  const customRulesEngine = new CustomRulesEngine();

  // List available tools
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [...TOOL_DEFINITIONS],
  }));

  // Handle tool calls
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args = {} } = request.params;
    try {
      return await dispatchTool(name, args, engine, customRulesEngine);
    } catch (error) {
      if (error instanceof McpError) throw error;
      const message = error instanceof Error ? error.message : 'Unknown error';
      throw new McpError(ErrorCode.InternalError, `Error executing ${name}: ${message}`);
    }
  });
}

async function handleAnalyzeProject(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path, languages, categories, severity, maxFiles } =
    parseAnalyzeProjectInput(args);
  const report = await engine.analyzeProject({
    path,
    languages,
    categories,
    severity,
    maxFiles,
  });
  return { content: [{ type: 'text', text: formatReport(report) }] };
}

async function handleAnalyzeFile(args: unknown): Promise<ToolResponse> {
  const { path } = parseAnalyzeFileInput(args);
  if (!(await fileExists(path))) {
    throw new McpError(ErrorCode.InvalidParams, `File not found: ${path}`);
  }
  const result = await analyzeFile(path);
  return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] };
}

async function handleGetDebtSummary(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path } = parseGetDebtSummaryInput(args);
  const report = await engine.analyzeProject({ path, maxFiles: 100 });

  const summary = `# Tech Debt Summary

**Project:** ${path}
**Analyzed Files:** ${report.project.analyzedFiles}
**Languages Detected:** ${report.project.languages.join(', ')}

## Health Score: ${report.summary.healthScore}/100

## Issues by Severity
- 🔴 Critical: ${report.summary.bySeverity.critical}
- 🟠 High: ${report.summary.bySeverity.high}
- 🟡 Medium: ${report.summary.bySeverity.medium}
- 🟢 Low: ${report.summary.bySeverity.low}

**Total Issues:** ${report.summary.totalIssues}
**Debt Score:** ${report.summary.debtScore}/100
`;

  return { content: [{ type: 'text', text: summary }] };
}

async function handleGetSqaleMetrics(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path, developmentTime: developmentTimeHours } = parseGetSqaleMetricsInput(args);
  const report = await engine.analyzeProject({ path, maxFiles: 100 });
  const sqale = report.sqale;

  let debtRatioText = 'N/A (provide developmentTime parameter to calculate)';
  if (developmentTimeHours !== undefined) {
    const debtRatioWithTime =
      (sqale.totalRemediationTime / (developmentTimeHours * 60)) * 100;
    debtRatioText = `${debtRatioWithTime.toFixed(1)}%`;
  }

  const ratingStars: Record<string, string> = {
    A: '⭐⭐⭐⭐⭐',
    B: '⭐⭐⭐⭐',
    C: '⭐⭐⭐',
    D: '⭐⭐',
    E: '⭐',
  };

  const sqaleReport = `# SQALE Technical Debt Metrics

**Project:** ${path}

## Overall Rating: ${sqale.rating} ${ratingStars[sqale.rating]}

**Total Remediation Time:** ${sqale.formattedTime}
**Debt Ratio:** ${debtRatioText}

## Breakdown by Severity
| Severity | Time |
|----------|------|
| Critical | ${formatMinutes(sqale.bySeverity.critical)} |
| High | ${formatMinutes(sqale.bySeverity.high)} |
| Medium | ${formatMinutes(sqale.bySeverity.medium)} |
| Low | ${formatMinutes(sqale.bySeverity.low)} |

## Breakdown by Category
| Category | Time |
|----------|------|
| code-quality | ${formatMinutes(sqale.byCategory['code-quality'])} |
| security | ${formatMinutes(sqale.byCategory.security)} |
| maintainability | ${formatMinutes(sqale.byCategory.maintainability)} |
| testing | ${formatMinutes(sqale.byCategory.testing)} |
| documentation | ${formatMinutes(sqale.byCategory.documentation)} |
| architecture | ${formatMinutes(sqale.byCategory.architecture)} |
| performance | ${formatMinutes(sqale.byCategory.performance)} |
| dependency | ${formatMinutes(sqale.byCategory.dependency)} |

## SQALE Rating Scale
- **A:** ≤ 5% debt ratio - Excellent
- **B:** 6-10% debt ratio - Good
- **C:** 11-20% debt ratio - Fair
- **D:** 21-50% debt ratio - Poor
- **E:** > 50% debt ratio - Critical
`;

  return { content: [{ type: 'text', text: sqaleReport }] };
}


function handleListSupportedLanguages(): ToolResponse {
  const languages = getSupportedLanguages();

  const languageList = languages.map(lang => {
    const config = LANGUAGE_CONFIGS[lang];
    return {
      id: lang,
      name: config.name,
      extensions: config.extensions,
      checks: config.specificChecks,
    };
  });

  const formatted = `# Supported Languages

${languageList.map(l =>
    `## ${l.name}\n` +
    `- **ID:** \`${l.id}\`\n` +
    `- **Extensions:** ${l.extensions.join(', ')}\n` +
    `- **Specific Checks:** ${l.checks.join(', ')}\n`,
  ).join('\n')}`;

  return { content: [{ type: 'text', text: formatted }] };
}

async function handleGetRecommendations(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path, limit = 5 } = parseGetRecommendationsInput(args);
  const report = await engine.analyzeProject({ path });
  const recommendations = report.recommendations.slice(0, limit);

  const formatted =
    `# Recommendations for Tech Debt Reduction\n\n` +
    recommendations
      .map(
        (r, i) =>
          `## ${i + 1}. ${r.title}\n\n` +
          `${r.description}\n\n` +
          `**Priority:** ${r.priority} | **Effort:** ${r.effort} | **Impact:** ${r.impact}\n\n` +
          `**Action Items:**\n${r.actionItems.map(a => `- ${a}`).join('\n')}\n`,
      )
      .join('\n---\n\n');

  return { content: [{ type: 'text', text: formatted }] };
}

async function handleGetIssuesBySeverity(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path, severity } = parseGetIssuesBySeverityInput(args);
  const report = await engine.analyzeProject({ path, severity });
  const issues = report.issues.filter(i => i.severity === severity);

  const issueLines = issues
    .slice(0, 50)
    .map(
      i =>
        `## ${i.title}\n` +
        `- **File:** ${i.file}${i.line ? `:${i.line}` : ''}\n` +
        `- **Category:** ${i.category}\n` +
        `- **Rule:** ${i.rule}\n` +
        `- **Description:** ${i.description}\n` +
        (i.suggestion ? `- **Suggestion:** ${i.suggestion}` : ''),
    )
    .join('\n---\n\n');

  const tail = issues.length > 50 ? `\n... and ${issues.length - 50} more issues.` : '';
  const formatted =
    `# ${severity.toUpperCase()} Severity Issues\n\n` +
    `Found **${issues.length}** ${severity} severity issues.\n\n` +
    issueLines +
    tail +
    '\n';

  return { content: [{ type: 'text', text: formatted }] };
}

async function handleGetIssuesByCategory(
  engine: AnalysisEngine,
  args: unknown,
): Promise<ToolResponse> {
  const { path, category } = parseGetIssuesByCategoryInput(args);
  const report = await engine.analyzeProject({ path, categories: [category] });
  const issues = report.issues;

  const issueLines = issues
    .slice(0, 50)
    .map(
      i =>
        `## ${i.title}\n` +
        `- **File:** ${i.file}${i.line ? `:${i.line}` : ''}\n` +
        `- **Severity:** ${i.severity}\n` +
        `- **Rule:** ${i.rule}\n` +
        `- **Description:** ${i.description}\n` +
        (i.suggestion ? `- **Suggestion:** ${i.suggestion}` : ''),
    )
    .join('\n---\n\n');

  const tail = issues.length > 50 ? `\n... and ${issues.length - 50} more issues.` : '';
  const formatted =
    `# ${category.toUpperCase()} Issues\n\n` +
    `Found **${issues.length}** issues in the ${category} category.\n\n` +
    issueLines +
    tail +
    '\n';

  return { content: [{ type: 'text', text: formatted }] };
}

// Custom Rules Handlers

async function handleAddCustomRule(
  customRulesEngine: CustomRulesEngine,
  args: unknown,
): Promise<ToolResponse> {
  const input = parseAddCustomRuleInput(args);

  const customPattern: CustomPattern = {
    id: input.id,
    pattern: input.pattern,
    message: input.message,
    severity: input.severity,
    category: input.category,
    suggestion: input.suggestion,
    languages: input.languages,
    flags: input.flags,
  };

  const validation = CustomRulesEngine.validatePattern(customPattern);
  if (!validation.valid) {
    const errors = validation.errors.map(e => `- ${e}`).join('\n');
    return {
      content: [{ type: 'text', text: `❌ Pattern validation failed:\n${errors}` }],
    };
  }

  customRulesEngine.addRule(customPattern);
  return {
    content: [{
      type: 'text',
      text:
        `✅ Custom rule '${input.id}' added successfully.\n\n` +
        `Rule: ${input.pattern}\nSeverity: ${input.severity}\nCategory: ${input.category}`,
    }],
  };
}

function handleRemoveCustomRule(
  customRulesEngine: CustomRulesEngine,
  args: unknown,
): ToolResponse {
  const { id } = parseRemoveCustomRuleInput(args);
  const removed = customRulesEngine.removeRule(id);
  if (removed) {
    return {
      content: [{ type: 'text', text: `✅ Custom rule '${id}' removed successfully.` }],
    };
  }
  return { content: [{ type: 'text', text: `❌ Custom rule '${id}' not found.` }] };
}

function handleListCustomRules(customRulesEngine: CustomRulesEngine): ToolResponse {
  const rules = customRulesEngine.getAllRules();
  const stats = customRulesEngine.getRuleStats();
  if (rules.length === 0) {
    return { content: [{ type: 'text', text: 'No custom rules defined.' }] };
  }

  const rulesText = rules
    .map(
      r =>
        `- **${r.id}**: ${r.message}\n` +
        `  - Pattern: \`${r.pattern}\`\n` +
        `  - Severity: ${r.severity}\n` +
        `  - Category: ${r.category}\n` +
        `  ${r.languages ? `- Languages: ${r.languages.join(', ')}` : '- Languages: All'}`,
    )
    .join('\n');

  const categoryStats = Object.entries(stats.byCategory)
    .filter(([, count]) => count > 0)
    .map(([cat, count]) => `  - ${cat}: ${count}`)
    .join('\n');

  const statsText =
    `\n## Statistics\n\n` +
    `- **Total Rules:** ${stats.totalRules}\n` +
    `- **By Severity:** Low: ${stats.bySeverity.low}, ` +
    `Medium: ${stats.bySeverity.medium}, ` +
    `High: ${stats.bySeverity.high}, ` +
    `Critical: ${stats.bySeverity.critical}\n` +
    `- **By Category:** \n${categoryStats}\n`;

  return { content: [{ type: 'text', text: `# Custom Rules\n\n${rulesText}\n${statsText}` }] };
}

async function handleExecuteCustomRules(
  customRulesEngine: CustomRulesEngine,
  args: unknown,
): Promise<ToolResponse> {
  const input = parseExecuteCustomRulesInput(args);
  let { path, code } = input;
  const { language } = input;

  if (!path && !code) {
    throw new McpError(ErrorCode.InvalidParams, 'Either path or code must be provided');
  }
  if (!code && path) {
    if (!await fileExists(path)) {
      throw new McpError(ErrorCode.InvalidParams, `File not found: ${path}`);
    }
    const stats = await getFileStats(path);
    if (!stats) {
      throw new McpError(
        ErrorCode.InvalidParams,
        `Could not read file stats for: ${path}`
      );
    }
    if (stats.size > MAX_FILE_SIZE_BYTES) {
      throw new McpError(
        ErrorCode.InvalidParams,
        `File exceeds maximum size of ${MAX_FILE_SIZE_BYTES} bytes for regex matching`
      );
    }
    code = await readFile(path);
  }
  if (!code) {
    throw new McpError(ErrorCode.InvalidParams, 'Could not read code from path or input');
  }

  const filePath = path || 'inline-code';
  const issues = customRulesEngine.executeRules(filePath, code, language);
  if (issues.length === 0) {
    return {
      content: [{ type: 'text', text: `✅ No custom rule violations found in ${filePath}.` }],
    };
  }

  const issueLines = issues
    .map(
      issue =>
        `## ${issue.title} [${issue.severity.toUpperCase()}]\n\n` +
        `**File:** ${issue.file}:${issue.line}\n` +
        `**Rule:** \`${issue.rule}\`\n` +
        `**Category:** ${issue.category}\n` +
        `**Suggestion:** ${issue.suggestion ?? 'N/A'}\n\n` +
        `${'```'}\n${issue.description}\n${'```'}`,
    )
    .join('\n\n---\n\n');

  const formatted =
    `# Custom Rule Violations in ${filePath}\n\n` +
    `Found ${issues.length} issue(s):\n\n` +
    issueLines;

  return { content: [{ type: 'text', text: formatted }] };
}

function handleValidateCustomPattern(args: unknown): ToolResponse {
  const input = parseValidateCustomPatternInput(args);
  const customPattern: CustomPattern = {
    id: input.id,
    pattern: input.pattern,
    message: input.message,
    severity: input.severity,
    category: input.category,
  };

  const validation = CustomRulesEngine.validatePattern(customPattern);
  if (validation.valid) {
    return {
      content: [{ type: 'text', text: `✅ Pattern is valid and can be used as a custom rule.` }],
    };
  }
  const errors = validation.errors.map(e => `- ${e}`).join('\n');
  return {
    content: [{ type: 'text', text: `❌ Pattern validation failed:\n${errors}` }],
  };
}
