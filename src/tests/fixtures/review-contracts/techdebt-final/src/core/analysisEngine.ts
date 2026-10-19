import {
  TechDebtReport,
  TechDebtIssue,
  DebtSummary,
  Recommendation,
  ProjectInfo,
  SupportedLanguage,
  AnalysisOptions,
  TechDebtConfig,
  Severity,
  DebtCategory,
} from '../types/index.js';
import { analyzeFile } from '../analyzers/index.js';
import { SQALEEngine } from './sqaleEngine.js';
import {
  getProjectFiles,
  loadConfig,
  findPackageFiles,
  getRelativePath,
} from '../utils/fileUtils.js';
import {
  detectLanguageFromExtension,
  getAllExtensions,
  getSupportedLanguages,
} from '../config/languages.js';

/**
 * Main analysis engine
 */
export class AnalysisEngine {
  private config: TechDebtConfig;

  constructor(config: TechDebtConfig = {}) {
    this.config = config;
  }

  /**
   * Analyze an entire project
   */
  async analyzeProject(options: AnalysisOptions): Promise<TechDebtReport> {
    const startTime = Date.now();
    const { path: projectPath } = options;

    // Load project config if exists
    const projectConfig = await loadConfig(projectPath);
    const mergedConfig = { ...this.config, ...projectConfig };

    // Get all relevant file extensions
    const extensions = getAllExtensions();

    // Find all files to analyze
    const files = await getProjectFiles(
      projectPath,
      extensions,
      mergedConfig.ignore
    );

    // Apply max files limit if specified
    const filesToAnalyze = options.maxFiles
      ? files.slice(0, options.maxFiles)
      : files;

    // Detect languages used in project
    const languagesUsed = new Set<SupportedLanguage>();
    for (const file of filesToAnalyze) {
      const lang = detectLanguageFromExtension(file);
      if (lang) {
        languagesUsed.add(lang);
      }
    }

    // Filter by requested languages if specified
    const targetLanguages = options.languages
      ? new Set(options.languages)
      : languagesUsed;

    // Analyze all files
    const allIssues: TechDebtIssue[] = [];
    let analyzedCount = 0;

    for (const file of filesToAnalyze) {
      const lang = detectLanguageFromExtension(file);
      if (!lang || !targetLanguages.has(lang)) continue;

      const fileIssues = await this.analyzeFileSafe(file, projectPath, mergedConfig, options);
      if (fileIssues === null) continue;

      allIssues.push(...fileIssues);
      analyzedCount++;
    }

    // Find package files
    const packageFiles = await findPackageFiles(projectPath);
    const packageManagers = this.detectPackageManagers(packageFiles);

    // Build project info
    const projectInfo: ProjectInfo = {
      path: projectPath,
      languages: Array.from(languagesUsed),
      totalFiles: files.length,
      analyzedFiles: analyzedCount,
      packageManagers,
      frameworks: [], // Could be enhanced to detect frameworks
    };

    // Calculate summary
    const summary = this.calculateSummary(allIssues);

    // Calculate SQALE metrics
    const sqaleEngine = new SQALEEngine();
    const sqale = sqaleEngine.calculateMetrics(allIssues);

    // Generate recommendations
    const recommendations = this.generateRecommendations(allIssues, summary);

    return {
      timestamp: new Date().toISOString(),
      project: projectInfo,
      summary,
      sqale,
      issues: allIssues,
      recommendations,
    };
  }

  /**
   * Analyze a single file, returning filtered issues or null on failure
   */
  private async analyzeFileSafe(
    file: string,
    projectPath: string,
    config: TechDebtConfig,
    options: AnalysisOptions
  ): Promise<TechDebtIssue[] | null> {
    try {
      const result = await analyzeFile(file, config);
      const relativePath = getRelativePath(projectPath, file);
      const issues = result.issues.map(issue => ({ ...issue, file: relativePath }));
      return this.filterIssues(issues, options);
    } catch {
      // Skip files that cannot be analyzed (e.g., encoding or permission errors).
      // Individual file failures should not abort the whole project scan.
      return null;
    }
  }

  /**
   * Apply severity and category filters to a list of issues
   */
  private filterIssues(issues: TechDebtIssue[], options: AnalysisOptions): TechDebtIssue[] {
    const { severity, categories } = options;
    const bySeverity = severity
      ? issues.filter(i => this.severityMeetsThreshold(i.severity, severity))
      : issues;
    return categories
      ? bySeverity.filter(i => categories.includes(i.category))
      : bySeverity;
  }

  /**
   * Check if severity meets threshold
   */
  private severityMeetsThreshold(severity: Severity, threshold: Severity): boolean {
    const levels: Severity[] = ['low', 'medium', 'high', 'critical'];
    return levels.indexOf(severity) >= levels.indexOf(threshold);
  }

  /**
   * Calculate summary statistics
   */
  private calculateSummary(issues: TechDebtIssue[]): DebtSummary {
    const bySeverity: Record<Severity, number> = {
      low: 0,
      medium: 0,
      high: 0,
      critical: 0,
    };

    const byCategory: Record<DebtCategory, number> = {
      'dependency': 0,
      'code-quality': 0,
      'architecture': 0,
      'documentation': 0,
      'testing': 0,
      'security': 0,
      'performance': 0,
      'maintainability': 0,
    };

    const byLanguage: Record<string, number> = {};

    for (const issue of issues) {
      bySeverity[issue.severity]++;
      byCategory[issue.category]++;

      if (issue.language) {
        byLanguage[issue.language] = (byLanguage[issue.language] || 0) + 1;
      }
    }

    // Calculate debt score (0-100, higher = more debt)
    const debtScore = this.calculateDebtScore(issues);
    const healthScore = 100 - debtScore;

    return {
      totalIssues: issues.length,
      bySeverity,
      byCategory,
      byLanguage,
      debtScore,
      healthScore,
    };
  }

  /**
   * Calculate debt score based on issues
   */
  private calculateDebtScore(issues: TechDebtIssue[]): number {
    if (issues.length === 0) return 0;

    const severityWeights: Record<Severity, number> = {
      low: 1,
      medium: 3,
      high: 7,
      critical: 15,
    };

    let totalWeight = 0;
    for (const issue of issues) {
      totalWeight += severityWeights[issue.severity];
    }

    // Normalize to 0-100 scale (cap at 100)
    // Assuming a baseline of 500 weighted points = 100 score
    const score = Math.min(100, (totalWeight / 500) * 100);
    return Math.round(score * 10) / 10;
  }

  /**
   * Generate recommendations based on issues
   */
  private generateRecommendations(
    issues: TechDebtIssue[],
    summary: DebtSummary
  ): Recommendation[] {
    const recommendations: Recommendation[] = [];

    // Critical issues recommendation
    if (summary.bySeverity.critical > 0) {
      recommendations.push({
        title: 'Address Critical Issues Immediately',
        description: `You have ${summary.bySeverity.critical} critical issues that should be fixed as soon as possible.`,
        priority: 1,
        effort: 'medium',
        impact: 'high',
        relatedIssues: issues
          .filter(i => i.severity === 'critical')
          .map(i => i.id),
        actionItems: [
          'Review all critical security issues',
          'Fix eval() and exec() usages',
          'Remove debugger statements',
          'Address format string vulnerabilities',
        ],
      });
    }

    // Security issues
    if (summary.byCategory.security > 0) {
      recommendations.push({
        title: 'Security Improvements Needed',
        description: `Found ${summary.byCategory.security} security-related issues.`,
        priority: 2,
        effort: 'medium',
        impact: 'high',
        relatedIssues: issues
          .filter(i => i.category === 'security')
          .map(i => i.id),
        actionItems: [
          'Run a security audit on the codebase',
          'Replace unsafe functions with safe alternatives',
          'Review and fix all security-related warnings',
        ],
      });
    }

    // TODO/FIXME cleanup
    const todoIssues = issues.filter(i => i.rule === 'todo-comment');
    if (todoIssues.length > 10) {
      recommendations.push({
        title: 'Clean Up TODO/FIXME Comments',
        description: `Found ${todoIssues.length} TODO/FIXME comments. Consider creating tracked issues.`,
        priority: 3,
        effort: 'small',
        impact: 'medium',
        relatedIssues: todoIssues.map(i => i.id),
        actionItems: [
          'Review all TODO comments',
          'Create GitHub issues for important TODOs',
          'Remove or complete outdated TODOs',
        ],
      });
    }

    // High issue count recommendation
    if (summary.bySeverity.high > 10) {
      recommendations.push({
        title: 'Dedicate Time for Tech Debt Reduction',
        description: `${summary.bySeverity.high} high-severity issues found. Consider allocating sprint time for tech debt.`,
        priority: 4,
        effort: 'large',
        impact: 'high',
        relatedIssues: issues
          .filter(i => i.severity === 'high')
          .map(i => i.id),
        actionItems: [
          'Schedule tech debt sprints',
          'Prioritize issues by business impact',
          'Set up automated code quality checks',
        ],
      });
    }

    // Maintainability recommendation
    if (summary.byCategory.maintainability > 5) {
      recommendations.push({
        title: 'Improve Code Maintainability',
        description: 'Several files have maintainability issues like deep nesting or excessive length.',
        priority: 5,
        effort: 'large',
        impact: 'medium',
        relatedIssues: issues
          .filter(i => i.category === 'maintainability')
          .map(i => i.id),
        actionItems: [
          'Break down large files into smaller modules',
          'Reduce function complexity',
          'Apply single responsibility principle',
        ],
      });
    }

    return recommendations.sort((a, b) => a.priority - b.priority);
  }

  /**
   * Detect package managers from package files
   */
  private detectPackageManagers(packageFiles: string[]): string[] {
    const managers = new Set<string>();

    for (const file of packageFiles) {
      if (file.includes('package.json')) managers.add('npm');
      if (file.includes('requirements.txt') || file.includes('setup.py') || file.includes('pyproject.toml')) managers.add('pip');
      if (file.includes('Pipfile')) managers.add('pipenv');
      if (file.includes('pom.xml')) managers.add('maven');
      if (file.includes('build.gradle')) managers.add('gradle');
      if (file.includes('Cargo.toml')) managers.add('cargo');
      if (file.includes('go.mod')) managers.add('go modules');
      if (file.includes('Gemfile')) managers.add('bundler');
      if (file.includes('composer.json')) managers.add('composer');
      if (file.includes('.csproj')) managers.add('nuget');
      if (file.includes('Package.swift')) managers.add('swift package manager');
      if (file.includes('Podfile')) managers.add('cocoapods');
      if (file.includes('CMakeLists.txt')) managers.add('cmake');
    }

    return Array.from(managers);
  }
}

/**
 * Get list of supported languages with their details
 */
export function getLanguageDetails(): Array<{
  id: SupportedLanguage;
  name: string;
  extensions: string[];
}> {
  const languages = getSupportedLanguages();
  const { LANGUAGE_CONFIGS } = require('../config/languages.js');

  return languages.map(lang => ({
    id: lang,
    name: LANGUAGE_CONFIGS[lang].name,
    extensions: LANGUAGE_CONFIGS[lang].extensions,
  }));
}
