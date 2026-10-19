import { CustomRulesEngine } from '../customRulesEngine.js';
import { CustomPattern } from '../../types/index.js';

describe('CustomRulesEngine', () => {
  let engine: CustomRulesEngine;

  beforeEach(() => {
    engine = new CustomRulesEngine();
  });

  describe('Rule Management', () => {
    it('adds a custom rule', () => {
      const pattern: CustomPattern = {
        id: 'test-rule',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      engine.addRule(pattern);
      expect(engine.hasRule('test-rule')).toBe(true);
    });

    it('retrieves a specific rule', () => {
      const pattern: CustomPattern = {
        id: 'test-rule',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      engine.addRule(pattern);
      const retrieved = engine.getRule('test-rule');
      expect(retrieved).toEqual(pattern);
    });

    it('removes a rule', () => {
      const pattern: CustomPattern = {
        id: 'test-rule',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      engine.addRule(pattern);
      expect(engine.hasRule('test-rule')).toBe(true);

      const removed = engine.removeRule('test-rule');
      expect(removed).toBe(true);
      expect(engine.hasRule('test-rule')).toBe(false);
    });

    it('returns false when removing non-existent rule', () => {
      const removed = engine.removeRule('non-existent');
      expect(removed).toBe(false);
    });

    it('gets all rules', () => {
      const pattern1: CustomPattern = {
        id: 'rule-1',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log',
      };

      const pattern2: CustomPattern = {
        id: 'rule-2',
        pattern: 'debugger',
        severity: 'high',
        category: 'code-quality',
        message: 'Debugger statement',
      };

      engine.addRule(pattern1);
      engine.addRule(pattern2);

      const rules = engine.getAllRules();
      expect(rules).toHaveLength(2);
    });

    it('clears all rules', () => {
      engine.addRule({
        id: 'rule-1',
        pattern: 'test',
        severity: 'low',
        category: 'code-quality',
        message: 'Test',
      });

      expect(engine.getAllRules()).toHaveLength(1);
      engine.clearRules();
      expect(engine.getAllRules()).toHaveLength(0);
    });
  });

  describe('Pattern Execution', () => {
    it('detects pattern matches in code', () => {
      const pattern: CustomPattern = {
        id: 'console-log',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      engine.addRule(pattern);
      const code = 'console.log("test");';
      const issues = engine.executeRules('test.ts', code);

      expect(issues).toHaveLength(1);
      expect(issues[0].rule).toBe('console-log');
      expect(issues[0].line).toBe(1);
    });

    it('detects multiple pattern matches', () => {
      const pattern: CustomPattern = {
        id: 'console-log',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      engine.addRule(pattern);
      const code = `console.log("test1");
console.log("test2");
console.log("test3");`;
      const issues = engine.executeRules('test.ts', code);

      expect(issues).toHaveLength(3);
      expect(issues[0].line).toBe(1);
      expect(issues[1].line).toBe(2);
      expect(issues[2].line).toBe(3);
    });

    it('respects language filter', () => {
      const pattern: CustomPattern = {
        id: 'ts-only-rule',
        pattern: 'any',
        severity: 'medium',
        category: 'code-quality',
        message: 'Any type found',
        languages: ['typescript'],
      };

      engine.addRule(pattern);
      const code = 'const x: any = 5;';

      // Should match when language is TypeScript
      const tsIssues = engine.executeRules('test.ts', code, 'typescript');
      expect(tsIssues).toHaveLength(1);

      // Should not match for other languages
      const jsIssues = engine.executeRules('test.js', code, 'javascript');
      expect(jsIssues).toHaveLength(0);
    });

    it('handles regex flags', () => {
      const pattern: CustomPattern = {
        id: 'case-insensitive',
        pattern: 'TODO',
        flags: 'i',
        severity: 'low',
        category: 'code-quality',
        message: 'TODO comment',
      };

      engine.addRule(pattern);
      const code = `// todo: fix this
// TODO: fix that
// Todo: fix this too`;
      const issues = engine.executeRules('test.ts', code);

      expect(issues).toHaveLength(3);
    });
  });

  describe('Pattern Validation', () => {
    it('validates valid pattern', () => {
      const pattern: CustomPattern = {
        id: 'test-rule',
        pattern: 'console\\.log',
        severity: 'low',
        category: 'code-quality',
        message: 'Console log found',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(true);
      expect(validation.errors).toHaveLength(0);
    });

    it('rejects pattern with invalid regex', () => {
      const pattern: CustomPattern = {
        id: 'invalid-rule',
        pattern: '[invalid(regex',
        severity: 'low',
        category: 'code-quality',
        message: 'Test',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(false);
      expect(validation.errors.length).toBeGreaterThan(0);
    });

    it('rejects pattern with missing required fields', () => {
      const pattern: Partial<CustomPattern> = {
        id: 'test',
        // Missing pattern, message, severity, category
      };

      const validation = CustomRulesEngine.validatePattern(pattern as CustomPattern);
      expect(validation.valid).toBe(false);
      expect(validation.errors.length).toBeGreaterThanOrEqual(3);
    });

    it('rejects pattern with invalid severity', () => {
      const pattern: CustomPattern = {
        id: 'test-rule',
        pattern: 'test',
        severity: 'invalid' as any,
        category: 'code-quality',
        message: 'Test',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(false);
    });

    it('rejects pattern whose length exceeds 1000 characters', () => {
      const pattern: CustomPattern = {
        id: 'long-rule',
        pattern: 'a'.repeat(1001),
        severity: 'low',
        category: 'code-quality',
        message: 'Too long',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(false);
      expect(validation.errors.some(e => e.includes('maximum length'))).toBe(true);
    });

    it('accepts pattern exactly at the 1000-character limit', () => {
      const pattern: CustomPattern = {
        id: 'boundary-rule',
        pattern: 'a'.repeat(1000),
        severity: 'low',
        category: 'code-quality',
        message: 'At boundary',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(true);
    });

    it('rejects flags containing disallowed characters', () => {
      const pattern: CustomPattern = {
        id: 'bad-flags-rule',
        pattern: 'test',
        flags: 'gx',  // 'x' is not a valid JS regex flag
        severity: 'low',
        category: 'code-quality',
        message: 'Bad flags',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      expect(validation.valid).toBe(false);
      expect(validation.errors.some(e => e.includes('Invalid regex flags'))).toBe(true);
    });

    it('accepts all valid flag characters', () => {
      const pattern: CustomPattern = {
        id: 'good-flags-rule',
        pattern: 'test',
        flags: 'gimsuy',
        severity: 'low',
        category: 'code-quality',
        message: 'All valid flags',
      };

      const validation = CustomRulesEngine.validatePattern(pattern);
      // This verifies the flags contain only valid JavaScript RegExp flag characters.
      expect(validation.valid).toBe(true);
      expect(validation.errors).toHaveLength(0);
    });

    it('strips disallowed flag characters at execution time for rules bypassing validation', () => {
      const engine = new CustomRulesEngine();
      const pattern: CustomPattern = {
        id: 'bad-flags-exec',
        pattern: 'hello',
        flags: 'gX',  // 'X' is disallowed — stripped to 'g' at runtime
        severity: 'low',
        category: 'code-quality',
        message: 'Bad flags at exec',
      };

      // Add directly (bypassing validatePattern) to simulate a config-loaded rule
      engine.addRule(pattern);
      // Should not throw and should still find the match
      const issues = engine.executeRules('test.ts', 'hello world');
      expect(issues.length).toBe(1);
    });
  });

  describe('Helper Methods', () => {
    it('creates simple pattern', () => {
      const pattern = CustomRulesEngine.createSimplePattern(
        'test-id',
        'test.*pattern',
        'Test message',
        'high',
        'security',
        'Fix this'
      );

      expect(pattern.id).toBe('test-id');
      expect(pattern.pattern).toBe('test.*pattern');
      expect(pattern.message).toBe('Test message');
      expect(pattern.severity).toBe('high');
      expect(pattern.category).toBe('security');
      expect(pattern.suggestion).toBe('Fix this');
    });

    it('calculates rule statistics', () => {
      engine.addRule({
        id: 'rule-1',
        pattern: 'test',
        severity: 'low',
        category: 'code-quality',
        message: 'Test 1',
      });

      engine.addRule({
        id: 'rule-2',
        pattern: 'test',
        severity: 'high',
        category: 'security',
        message: 'Test 2',
      });

      const stats = engine.getRuleStats();
      expect(stats.totalRules).toBe(2);
      expect(stats.bySeverity.low).toBe(1);
      expect(stats.bySeverity.high).toBe(1);
      expect(stats.byCategory['code-quality']).toBe(1);
      expect(stats.byCategory.security).toBe(1);
    });
  });

  describe('Constructor', () => {
    it('initializes with patterns', () => {
      const patterns: CustomPattern[] = [
        {
          id: 'rule-1',
          pattern: 'console\\.log',
          severity: 'low',
          category: 'code-quality',
          message: 'Console log',
        },
        {
          id: 'rule-2',
          pattern: 'debugger',
          severity: 'high',
          category: 'code-quality',
          message: 'Debugger',
        },
      ];

      const customEngine = new CustomRulesEngine(patterns);
      expect(customEngine.getAllRules()).toHaveLength(2);
    });
  });

  describe('Zero-length match guard', () => {
    it('terminates and emits one issue per non-empty match for patterns like .*', () => {
      const pattern: CustomPattern = {
        id: 'empty-match-rule',
        pattern: '.*',
        severity: 'low',
        category: 'code-quality',
        message: 'Match anything',
      };

      engine.addRule(pattern);
      const code = 'hello world';
      // .* matches the full line once (non-empty), then an empty string at end-of-line.
      // The zero-length match guard skips the empty match, so exactly 1 issue per line.
      const issues = engine.executeRules('test.ts', code);
      expect(issues.length).toBe(1);
    });

    it('emits no issues for a pure zero-length pattern (?:) with global flag', () => {
      const pattern: CustomPattern = {
        id: 'empty-group-rule',
        pattern: '(?:)',
        flags: 'g',
        severity: 'low',
        category: 'code-quality',
        message: 'Empty non-capturing group',
      };

      engine.addRule(pattern);
      const code = 'abc';
      // Every match is zero-length, so all are skipped — no issues emitted, no infinite loop.
      const issues = engine.executeRules('test.ts', code);
      expect(issues.length).toBe(0);
    });
  });

  describe('MAX_PATTERN_LENGTH defense-in-depth at execution time', () => {
    it('returns empty array and calls onRuleError for oversized patterns added directly via addRule', () => {
      const errors: Array<{ id: string; error: Error }> = [];
      const engineWithErrCb = new CustomRulesEngine(
        undefined,
        (id, error) => errors.push({ id, error })
      );

      const oversizedPattern: CustomPattern = {
        id: 'oversized-rule',
        pattern: 'a'.repeat(1_001),
        severity: 'low',
        category: 'code-quality',
        message: 'Oversized',
      };

      // Bypass validatePattern by calling addRule directly
      engineWithErrCb.addRule(oversizedPattern);
      const issues = engineWithErrCb.executeRules('test.ts', 'aaaa');
      expect(issues).toHaveLength(0);
      expect(errors).toHaveLength(1);
      expect(errors[0].id).toBe('oversized-rule');
      expect(errors[0].error.message).toMatch(/exceeds maximum length/);
    });

    it('executes a pattern at exactly MAX_PATTERN_LENGTH without error', () => {
      const exactPattern: CustomPattern = {
        id: 'exact-length-rule',
        pattern: 'a'.repeat(1_000),
        severity: 'low',
        category: 'code-quality',
        message: 'Exact length',
      };

      engine.addRule(exactPattern);
      // Should not throw and returns results normally (no match in this content)
      expect(() => engine.executeRules('test.ts', 'hello')).not.toThrow();
    });
  });

  describe('Error Handling', () => {
    it('handles invalid regex gracefully', () => {
      const pattern: CustomPattern = {
        id: 'bad-rule',
        pattern: '[invalid',
        severity: 'low',
        category: 'code-quality',
        message: 'Test',
      };

      engine.addRule(pattern);
      // Should not throw, just skip the invalid rule
      const issues = engine.executeRules('test.ts', 'const x = 5;');
      expect(issues).toHaveLength(0);
    });

    it('returns empty array when no rules exist', () => {
      const issues = engine.executeRules('test.ts', 'const x = 5;');
      expect(issues).toEqual([]);
    });
  });
});

