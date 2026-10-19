/**
 * Config validation handler for .techdebtrc.json files
 */

import { ErrorCode, McpError } from '@modelcontextprotocol/sdk/types.js';
import { CustomRulesEngine } from '../core/customRulesEngine.js';
import { fileExists } from '../utils/fileUtils.js';
import { readFile as fsReadFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { CustomPattern } from '../types/index.js';
import { isRecord, requireRecord } from './argValidation.js';

/** Type guard: checks that a value satisfies the minimum required shape of a CustomPattern. */
function isCustomPatternShape(value: unknown): value is CustomPattern {
  if (!isRecord(value)) return false;
  return (
    typeof value['id'] === 'string' &&
    typeof value['pattern'] === 'string' &&
    typeof value['severity'] === 'string' &&
    typeof value['category'] === 'string' &&
    typeof value['message'] === 'string'
  );
}

/** Known valid keys for .techdebtrc.json */
const VALID_CONFIG_KEYS = new Set(['ignore', 'include', 'rules', 'severity', 'ruleExclusions', 'customPatterns', 'languageOverrides']);
const VALID_RULE_KEYS = new Set(['maxFileLines', 'maxFunctionLines', 'maxComplexity', 'maxNestingDepth', 'maxParameters', 'minCommentRatio']);
const VALID_SEVERITIES = new Set(['low', 'medium', 'high', 'critical']);

/**
 * Validate that a config field is an array of strings.
 */
function validateStringArrayField(
  key: string,
  config: Record<string, unknown>,
  errors: string[],
): void {
  if (!(key in config)) return;
  const value = config[key];
  if (!Array.isArray(value)) {
    errors.push(`"${key}" must be an array of glob strings`);
    return;
  }
  if (value.some((v: unknown) => typeof v !== 'string')) {
    errors.push(`"${key}" array must contain only strings`);
  }
}

/**
 * Validate the "rules" field of the config.
 */
function validateRulesField(
  config: Record<string, unknown>,
  errors: string[],
  warnings: string[],
): void {
  if (!('rules' in config)) return;
  if (!isRecord(config.rules)) {
    errors.push('"rules" must be an object');
    return;
  }
  const rules = config.rules;
  for (const key of Object.keys(rules)) {
    if (!VALID_RULE_KEYS.has(key)) warnings.push(`Unknown rule key: "${key}"`);
  }
  for (const key of VALID_RULE_KEYS) {
    if (key in rules && typeof rules[key] !== 'number') {
      errors.push(`"rules.${key}" must be a number`);
    }
  }
}

/**
 * Validate the "severity" field of the config.
 */
function validateSeverityField(config: Record<string, unknown>, errors: string[]): void {
  if (!('severity' in config)) return;
  if (!isRecord(config.severity)) {
    errors.push('"severity" must be an object');
    return;
  }
  for (const [rule, level] of Object.entries(config.severity)) {
    if (typeof level !== 'string' || !VALID_SEVERITIES.has(level)) {
      errors.push(`"severity.${rule}" has invalid value "${level}" — must be a string: low, medium, high, critical`);
    }
  }
}

/**
 * Validate the "ruleExclusions" field of the config.
 */
function validateRuleExclusionsField(config: Record<string, unknown>, errors: string[]): void {
  if (!('ruleExclusions' in config)) return;
  if (!isRecord(config.ruleExclusions)) {
    errors.push('"ruleExclusions" must be an object mapping rule names to arrays of glob strings');
    return;
  }
  for (const [rule, patterns] of Object.entries(config.ruleExclusions)) {
    if (!Array.isArray(patterns)) {
      errors.push(`"ruleExclusions.${rule}" must be an array of glob strings`);
    } else if (patterns.some((v: unknown) => typeof v !== 'string')) {
      errors.push(`"ruleExclusions.${rule}" array must contain only strings`);
    }
  }
}

/**
 * Validate a single custom pattern entry and push any errors.
 */
function validateCustomPattern(p: unknown, i: number, errors: string[]): void {
  if (!isRecord(p)) {
    errors.push(`customPatterns[${i}]: must be an object`);
    return;
  }
  if (!isCustomPatternShape(p)) {
    errors.push(`customPatterns[${i}]: missing required fields (id, pattern, severity, category, message)`);
    return;
  }
  const result = CustomRulesEngine.validatePattern(p);
  if (!result.valid) {
    result.errors.forEach(e => errors.push(`customPatterns[${i}] (${p.id}): ${e}`));
  }
}

/**
 * Validate the "customPatterns" field of the config.
 */
function validateCustomPatternsField(value: unknown, errors: string[]): void {
  if (!Array.isArray(value)) {
    errors.push('"customPatterns" must be an array');
    return;
  }
  value.forEach((p: unknown, i: number) => validateCustomPattern(p, i, errors));
}

/**
 * Validate a .techdebtrc.json configuration file
 */
export async function handleValidateConfig(args: unknown): Promise<{ content: Array<{ type: string; text: string }> }> {
  const a = requireRecord(args);
  if (typeof a.path !== 'string') {
    throw new McpError(ErrorCode.InvalidParams, 'Missing or invalid required parameter: path (must be a string)');
  }
  const inputPath = a.path;
  if (!(await fileExists(inputPath))) {
    throw new McpError(ErrorCode.InvalidParams, `Path not found: ${inputPath}`);
  }

  const pathStat = await stat(inputPath);
  const configPath = pathStat.isDirectory()
    ? join(inputPath, '.techdebtrc.json')
    : inputPath;

  if (!(await fileExists(configPath))) {
    return { content: [{ type: 'text', text: `⚠️ No .techdebtrc.json found at:\n  ${configPath}\n\nCreate one to customize tech debt analysis for your project.` }] };
  }

  const errors: string[] = [];
  const warnings: string[] = [];
  let config: Record<string, unknown>;

  try {
    const raw = await fsReadFile(configPath, 'utf-8');
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) {
      return { content: [{ type: 'text', text: `❌ Invalid config in ${configPath}:\n  Top-level value must be a JSON object, got ${parsed === null ? 'null' : Array.isArray(parsed) ? 'array' : typeof parsed}.` }] };
    }
    config = parsed;
  } catch (err) {
    return { content: [{ type: 'text', text: `❌ Invalid JSON in ${configPath}:\n  ${err instanceof Error ? err.message : String(err)}` }] };
  }

  for (const key of Object.keys(config)) {
    if (!VALID_CONFIG_KEYS.has(key)) {
      warnings.push(`Unknown top-level key: "${key}"`);
    }
  }

  validateStringArrayField('ignore', config, errors);
  validateStringArrayField('include', config, errors);
  validateRulesField(config, errors, warnings);
  validateSeverityField(config, errors);
  validateRuleExclusionsField(config, errors);

  if ('languageOverrides' in config && !isRecord(config.languageOverrides)) {
    errors.push('"languageOverrides" must be an object keyed by language name');
  }

  if ('customPatterns' in config) {
    validateCustomPatternsField(config.customPatterns, errors);
  }

  if (errors.length === 0 && warnings.length === 0) {
    return { content: [{ type: 'text', text: `✅ ${configPath} is valid.\n\nAll fields pass schema validation.` }] };
  }

  let report = `# Config Validation: ${configPath}\n\n`;
  if (errors.length > 0) {
    report += `## ❌ Errors (${errors.length})\n${errors.map(e => `- ${e}`).join('\n')}\n\n`;
  }
  if (warnings.length > 0) {
    report += `## ⚠️ Warnings (${warnings.length})\n${warnings.map(w => `- ${w}`).join('\n')}\n\n`;
  }
  report += errors.length > 0 ? '**Fix errors before using this config.**' : '**Config is usable — address warnings to avoid surprises.**';

  return { content: [{ type: 'text', text: report }] };
}
