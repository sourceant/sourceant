// Tech Debt Categories
export type DebtCategory =
  | 'dependency'
  | 'code-quality'
  | 'architecture'
  | 'documentation'
  | 'testing'
  | 'security'
  | 'performance'
  | 'maintainability';

// Severity levels
export type Severity = 'low' | 'medium' | 'high' | 'critical';

// Effort estimation
export type Effort = 'trivial' | 'small' | 'medium' | 'large' | 'xlarge';

// Supported languages
export type SupportedLanguage =
  | 'javascript'
  | 'typescript'
  | 'python'
  | 'java'
  | 'swift'
  | 'kotlin'
  | 'objectivec'
  | 'cpp'
  | 'c'
  | 'csharp'
  | 'go'
  | 'rust'
  | 'ruby'
  | 'php';

// Language configuration
export interface LanguageConfig {
  name: string;
  extensions: string[];
  packageFiles: string[];
  commentPatterns: {
    single: string[];
    multiStart: string[];
    multiEnd: string[];
  };
  todoPatterns: RegExp[];
  specificChecks: string[];
}

// Tech debt issue
export interface TechDebtIssue {
  id: string;
  category: DebtCategory;
  severity: Severity;
  file: string;
  line?: number;
  endLine?: number;
  column?: number;
  title: string;
  description: string;
  code?: string;
  suggestion?: string;
  effort?: Effort;
  language?: SupportedLanguage;
  rule: string;
  tags?: string[];
}

// Recommendation
export interface Recommendation {
  title: string;
  description: string;
  priority: number;
  effort: Effort;
  impact: 'low' | 'medium' | 'high';
  relatedIssues: string[];
  actionItems: string[];
}

// Project info
export interface ProjectInfo {
  path: string;
  languages: SupportedLanguage[];
  totalFiles: number;
  analyzedFiles: number;
  packageManagers: string[];
  frameworks: string[];
}

// Debt summary
export interface DebtSummary {
  totalIssues: number;
  bySeverity: Record<Severity, number>;
  byCategory: Record<DebtCategory, number>;
  byLanguage: Record<string, number>;
  debtScore: number; // 0-100, higher = more debt
  healthScore: number; // 0-100, higher = healthier
}

// Full report
export interface TechDebtReport {
  timestamp: string;
  project: ProjectInfo;
  summary: DebtSummary;
  sqale: SQALEMetrics;
  issues: TechDebtIssue[];
  recommendations: Recommendation[];
}

// Analysis options
export interface AnalysisOptions {
  path: string;
  languages?: SupportedLanguage[];
  categories?: DebtCategory[];
  severity?: Severity;
  includeTests?: boolean;
  maxFiles?: number;
  ignorePaths?: string[];
}

// File analysis result
export interface FileAnalysisResult {
  file: string;
  language: SupportedLanguage | null;
  issues: TechDebtIssue[];
  metrics: FileMetrics;
}

// File metrics
export interface FileMetrics {
  lines: number;
  codeLines: number;
  commentLines: number;
  blankLines: number;
  complexity?: number;
  functions?: number;
  classes?: number;
}

// Dependency info
export interface DependencyInfo {
  name: string;
  currentVersion: string;
  latestVersion?: string;
  isOutdated: boolean;
  isDeprecated: boolean;
  hasVulnerabilities: boolean;
  vulnerabilities?: VulnerabilityInfo[];
}

// Vulnerability info
export interface VulnerabilityInfo {
  id: string;
  severity: Severity;
  title: string;
  description: string;
  fixedIn?: string;
}

// Configuration file structure
export interface TechDebtConfig {
  ignore?: string[];
  include?: string[];
  severity?: {
    [rule: string]: Severity;
  };
  rules?: {
    maxFileLines?: number;
    maxFunctionLines?: number;
    maxComplexity?: number;
    maxParameters?: number;
    maxNestingDepth?: number;
    minCommentRatio?: number;
  };
  ruleExclusions?: {
    [rule: string]: string[];
  };
  customPatterns?: CustomPattern[];
  languageOverrides?: {
    [lang: string]: Partial<LanguageConfig>;
  };
}

// Custom pattern for user-defined checks
export interface CustomPattern {
  id: string;
  pattern: string;
  flags?: string;
  severity: Severity;
  category: DebtCategory;
  message: string;
  suggestion?: string;
  languages?: SupportedLanguage[];
}

// SQALE Rating (A-E scale like SonarQube)
export type SQALERating = 'A' | 'B' | 'C' | 'D' | 'E';

// Effort to time mapping (in minutes)
export interface EffortTimeMapping {
  trivial: number;  // <= 5 minutes
  small: number;    // 5-30 minutes
  medium: number;   // 30 min - 2 hours
  large: number;    // 2-4 hours
  xlarge: number;   // 8 hours (1 day)
}

// SQALE Metrics
export interface SQALEMetrics {
  // Total remediation time in minutes
  totalRemediationTime: number;

  // Formatted time (e.g., "2h 30m" or "3d 4h")
  formattedTime: string;

  // Debt ratio (remediation time / development time estimate)
  debtRatio: number;

  // Rating based on debt ratio thresholds
  rating: SQALERating;

  // Breakdown by category
  byCategory: Record<DebtCategory, number>;

  // Breakdown by severity
  bySeverity: Record<Severity, number>;
}

// MCP Resource response shape
export type ResourceResponse = { contents: Array<{ uri: string; mimeType: string; text: string }> };

// ========================================
// Phase 2: Dependency Analysis Types
// ========================================

/**
 * Supported package managers for dependency analysis
 */
export type PackageManager =
  | 'npm'
  | 'pip'
  | 'maven'
  | 'gradle'
  | 'cargo'
  | 'go'
  | 'composer'
  | 'bundler'
  | 'conan'
  | 'vcpkg'
  | 'swift'
  | 'nuget'
  | 'cpp';

/**
 * Parsed dependency information from manifest files
 */
export interface ParsedDependency {
  name: string;
  version: string;
  isDev: boolean;
  source: string; // e.g., "package.json", "requirements.txt"
}

/**
 * Dependency analysis report containing all parsed dependencies
 */
export interface DependencyReport {
  timestamp: string;
  projectPath: string;
  dependencies: ParsedDependency[];
  packageManagers: PackageManager[];
  summary: {
    totalDependencies: number;
    productionDependencies: number;
    developmentDependencies: number;
    ecosystems: PackageManager[];
  };
  issues?: TechDebtIssue[]; // Outdated/vulnerable dependencies as issues
}
