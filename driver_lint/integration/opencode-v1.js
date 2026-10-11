// driver-lint OpenCode V1 adapter. Local plugin; no API calls or shell eval.
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';

const execute = promisify(execFile);
const writes = new Set(['edit', 'write', 'apply_patch', 'bash']);
const component = '@DRIVER_LINT_COMPONENT@';

export const DriverLint = async ({ directory, worktree }) => {
  const root = worktree && worktree !== '/' ? worktree : directory;
  if (!root) throw new Error('Driver lint needs a project directory');
  return {
    'tool.execute.after': async (input, output) => {
      if (!writes.has(input.tool)) return;
      let report;
      try {
        const unit = component.startsWith('@') ? [] : ['--component', component];
        const result = await execute('python3', [path.join(root, 'driver_lint/lint.py'), ...unit, 'scan', '--root', root],
          { cwd: root, timeout: 120000, maxBuffer: 8 * 1024 * 1024 });
        report = result.stdout;
      } catch (error) {
        // Post-edit feedback does not undo a write. The actual Git hook rejects
        // commits. Preserve exit 1/2 as visible verification failure.
        report = `DRIVER LINT FAILED (exit ${error.code ?? 'unknown'}).\n${error.stdout ?? ''}${error.stderr ?? error.message}`;
      }
      const feedback = `\n\n[Driver lint ${component}]\n${report.slice(0, 12000)}`;
      // Built-ins expose output; MCP-style results can instead expose content.
      // Preserve the original parts and annotate both representations if present.
      output.output = `${output.output ?? ''}${feedback}`;
      if (Array.isArray(output.content)) {
        output.content = [...output.content, { type: 'text', text: feedback }];
      }
    },
  };
};
