import * as fs from 'fs';
import * as path from 'path';
import { glob } from 'glob';
import ignore, { Ignore } from 'ignore';
import { TechDebtConfig } from '../types/index.js';

/**
 * Default patterns to ignore
 */
export const DEFAULT_IGNORE_PATTERNS = [
  'node_modules/**',
  '.git/**',
  'dist/**',
  'build/**',
  'out/**',
  '.next/**',
  '.nuxt/**',
  'coverage/**',
  '__pycache__/**',
  '*.pyc',
  '.pytest_cache/**',
  'venv/**',
  '.venv/**',
  'env/**',
  '.env/**',
  'target/**',
  'bin/**',
  'obj/**',
  'packages/**',
  '.idea/**',
  '.vscode/**',
  '*.min.js',
  '*.min.css',
  '*.map',
  '*.lock',
  'package-lock.json',
  'yarn.lock',
  'pnpm-lock.yaml',
  'Pods/**',
  'Carthage/**',
  '.build/**',
  'DerivedData/**',
  '*.xcworkspace/**',
  '*.xcodeproj/**',
  'vendor/**',
];

/**
 * Read file contents
 */
export async function readFile(filePath: string): Promise<string> {
  return fs.promises.readFile(filePath, 'utf-8');
}

/**
 * Check if file exists
 */
export async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.promises.access(filePath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Read JSON file
 */
export async function readJsonFile<T>(filePath: string): Promise<T | null> {
  try {
    const content = await readFile(filePath);
    return JSON.parse(content) as T;
  } catch {
    return null;
  }
}

/**
 * Load .gitignore patterns
 */
export async function loadGitignore(projectPath: string): Promise<Ignore> {
  const ig = ignore();
  const gitignorePath = path.join(projectPath, '.gitignore');

  if (await fileExists(gitignorePath)) {
    const content = await readFile(gitignorePath);
    ig.add(content);
  }

  return ig;
}

/**
 * Load tech debt config
 */
export async function loadConfig(projectPath: string): Promise<TechDebtConfig | null> {
  const configNames = [
    '.techdebtrc.json',
    '.techdebtrc',
    'techdebt.config.json',
    '.techdebt.json',
  ];

  for (const configName of configNames) {
    const configPath = path.join(projectPath, configName);
    if (await fileExists(configPath)) {
      return readJsonFile<TechDebtConfig>(configPath);
    }
  }

  return null;
}

/**
 * Get all files in a directory matching criteria
 */
export async function getProjectFiles(
  projectPath: string,
  extensions: string[],
  customIgnore?: string[]
): Promise<string[]> {
  // Build glob pattern for extensions
  const extPattern = extensions.length === 1
    ? `**/*${extensions[0]}`
    : `**/*{${extensions.join(',')}}`;

  // Combine ignore patterns
  const ignorePatterns = [...DEFAULT_IGNORE_PATTERNS];
  if (customIgnore) {
    ignorePatterns.push(...customIgnore);
  }

  // Load .gitignore
  const gitIgnore = await loadGitignore(projectPath);

  // Find files
  const files = await glob(extPattern, {
    cwd: projectPath,
    absolute: true,
    nodir: true,
    ignore: ignorePatterns,
  });

  // Filter by gitignore
  return files.filter(file => {
    const relativePath = path.relative(projectPath, file);
    return !gitIgnore.ignores(relativePath);
  });
}

/**
 * Get file stats
 */
export async function getFileStats(filePath: string): Promise<fs.Stats | null> {
  try {
    return await fs.promises.stat(filePath);
  } catch {
    return null;
  }
}

/**
 * Count lines in content
 */
export function countLines(content: string): {
  total: number;
  code: number;
  comment: number;
  blank: number;
} {
  const lines = content.split('\n');
  let code = 0;
  let comment = 0;
  let blank = 0;
  let inBlockComment = false;

  for (const line of lines) {
    const trimmed = line.trim();

    if (trimmed === '') {
      blank++;
      continue;
    }

    // Simple block comment detection
    if (trimmed.startsWith('/*') && !trimmed.includes('*/')) {
      inBlockComment = true;
      comment++;
      continue;
    }

    if (inBlockComment) {
      comment++;
      if (trimmed.includes('*/')) {
        inBlockComment = false;
      }
      continue;
    }

    // Single line comments
    if (
      trimmed.startsWith('//') ||
      trimmed.startsWith('#') ||
      trimmed.startsWith('--') ||
      trimmed.startsWith('"""') ||
      trimmed.startsWith("'''")
    ) {
      comment++;
      continue;
    }

    code++;
  }

  return {
    total: lines.length,
    code,
    comment,
    blank,
  };
}

/**
 * Get relative path from project root
 */
export function getRelativePath(projectPath: string, filePath: string): string {
  return path.relative(projectPath, filePath);
}

/**
 * Find package manager files in project
 */
export async function findPackageFiles(projectPath: string): Promise<string[]> {
  const packageFilePatterns = [
    'package.json',
    'requirements.txt',
    'setup.py',
    'pyproject.toml',
    'Pipfile',
    'pom.xml',
    'build.gradle',
    'build.gradle.kts',
    'Cargo.toml',
    'go.mod',
    'Gemfile',
    'composer.json',
    '*.csproj',
    'Package.swift',
    'Podfile',
    'CMakeLists.txt',
  ];

  const foundFiles: string[] = [];

  for (const pattern of packageFilePatterns) {
    const files = await glob(pattern, {
      cwd: projectPath,
      absolute: true,
      nodir: true,
    });
    foundFiles.push(...files);
  }

  return foundFiles;
}
