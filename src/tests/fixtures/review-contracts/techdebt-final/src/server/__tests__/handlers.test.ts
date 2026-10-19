import { jest } from '@jest/globals';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { McpError } from '@modelcontextprotocol/sdk/types.js';
import { attachHandlers } from '../handlers.js';
import { fileExists, getFileStats } from '../../utils/fileUtils.js';
import { MAX_FILE_SIZE_BYTES } from '../../core/customRulesEngine.js';

// Mock the dependencies
jest.mock('../../core/analysisEngine.js');
jest.mock('../../core/customRulesEngine.js', () => ({
  CustomRulesEngine: class {
    addRule = jest.fn();
    removeRule = jest.fn(() => false);
    getAllRules = jest.fn(() => []);
    hasRule = jest.fn(() => false);
    getRuleStats = jest.fn(() => ({ totalRules: 0, bySeverity: {}, byCategory: {} }));
    executeRules = jest.fn(() => []);
  },
  MAX_CODE_LENGTH: 500_000,
  MAX_FILE_SIZE_BYTES: 500_000,
}));
jest.mock('../../analyzers/index.js');
jest.mock('../../analyzers/dependencies/index.js');
jest.mock('../../config/languages.js');
jest.mock('../../utils/fileUtils.js', () => ({
  readFile: jest.fn(),
  fileExists: jest.fn(),
  getFileStats: jest.fn(),
  getRelativePath: jest.fn((_b: string, f: string) => f),
}));

const mockFileExists = fileExists as jest.MockedFunction<typeof fileExists>;
const mockGetFileStats = getFileStats as jest.MockedFunction<typeof getFileStats>;

describe('Handlers', () => {
  let mockServer: McpServer;
  let callToolHandler: (request: any) => Promise<any>;

  beforeEach(() => {
    // Reset all mocks
    jest.clearAllMocks();

    // Create mock server that captures the registered handlers
    mockServer = {
      server: {
        setRequestHandler: jest.fn((schema: any, handler: any) => {
          // Capture the CallToolRequestSchema handler (second call)
          if (schema?.method === 'tools/call' || typeof handler === 'function') {
            callToolHandler = handler;
          }
        }),
      },
    } as any;
  });

  describe('attachHandlers', () => {
    it('should attach handlers to the server', () => {
      attachHandlers(mockServer);

      expect(mockServer.server.setRequestHandler).toHaveBeenCalledTimes(2);
    });
  });

  describe('execute_custom_rules file size guard', () => {
    beforeEach(() => {
      attachHandlers(mockServer);
      // The second setRequestHandler call receives the CallToolRequestSchema handler
      const calls = (mockServer.server.setRequestHandler as jest.MockedFunction<any>).mock.calls;
      callToolHandler = calls[calls.length - 1][1];
    });

    it('rejects a path input whose file size exceeds MAX_FILE_SIZE_BYTES', async () => {
      mockFileExists.mockResolvedValue(true);
      mockGetFileStats.mockResolvedValue({ size: MAX_FILE_SIZE_BYTES + 1 } as any);

      await expect(
        callToolHandler({
          params: {
            name: 'execute_custom_rules',
            arguments: { path: '/some/large/file.ts' },
          },
        })
      ).rejects.toThrow(McpError);
    });

    it('accepts a path input whose file size is exactly MAX_FILE_SIZE_BYTES', async () => {
      mockFileExists.mockResolvedValue(true);
      mockGetFileStats.mockResolvedValue({ size: MAX_FILE_SIZE_BYTES } as any);
      // readFile needs to return something — mock it
      const { readFile } = await import('../../utils/fileUtils.js');
      (readFile as jest.MockedFunction<typeof readFile>).mockResolvedValue('const x = 1;');

      await expect(
        callToolHandler({
          params: {
            name: 'execute_custom_rules',
            arguments: { path: '/some/file.ts' },
          },
        })
      ).resolves.toBeDefined();
    });
  });

  describe('Tool Definitions', () => {
    it('should export all required tools', () => {
      const { TOOL_DEFINITIONS } = require('../tools.js');

      expect(TOOL_DEFINITIONS).toBeDefined();
      expect(Array.isArray(TOOL_DEFINITIONS)).toBe(true);
      expect(TOOL_DEFINITIONS.length).toBeGreaterThan(0);

      // Check that each tool has required properties
      TOOL_DEFINITIONS.forEach((tool: any) => {
        expect(tool).toHaveProperty('name');
        expect(tool).toHaveProperty('description');
        expect(tool).toHaveProperty('inputSchema');
        expect(tool.inputSchema).toHaveProperty('type', 'object');
        expect(tool.inputSchema).toHaveProperty('properties');
      });
    });

    it('should include all expected tools', () => {
      const { TOOL_DEFINITIONS } = require('../tools.js');
      const toolNames = TOOL_DEFINITIONS.map((tool: any) => tool.name);

      const expectedTools = [
        'analyze_project',
        'analyze_file',
        'get_debt_summary',
        'get_sqale_metrics',
        'list_supported_languages',
        'get_recommendations',
        'get_issues_by_severity',
        'get_issues_by_category',
        'add_custom_rule',
        'remove_custom_rule',
        'list_custom_rules',
        'execute_custom_rules',
        'validate_custom_pattern',
        'check_dependencies',
        'validate_config',
        'get_vulnerability_report',
      ];

      expectedTools.forEach(toolName => {
        expect(toolNames).toContain(toolName);
      });
    });
  });
});
