// driver-lint OpenCode V2 adapter; install only with the V2 plugin API.
import { Plugin } from '@opencode/plugin';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';

const execute = promisify(execFile);
const writes = new Set(['edit', 'write', 'apply_patch', 'bash']);
const component = '@DRIVER_LINT_COMPONENT@';

export default Plugin.define({
  id: `driver-lint-${component}`,
  async setup(ctx) {
    const root = ctx.location.directory;
    await ctx.tool.hook('execute.after', async (event) => {
      if (!writes.has(event.tool)) return;
      let report;
      try {
        const unit = component.startsWith('@') ? [] : ['--component', component];
        const result = await execute('python3', [path.join(root, 'driver_lint/lint.py'), ...unit, 'scan', '--root', root],
          { cwd: root, timeout: 120000, maxBuffer: 8 * 1024 * 1024 });
        report = result.stdout;
      } catch (error) {
        report = `DRIVER LINT FAILED (exit ${error.code ?? 'unknown'}).\n${error.stdout ?? ''}${error.stderr ?? error.message}`;
      }
      if (event.status === 'completed') {
        const content = event.result.content;
        const feedback = `\n\n[Driver lint]\n${report.slice(0, 12000)}`;
        if (typeof content === 'string') {
          event.result = { ...event.result, content: content + feedback };
        } else if (Array.isArray(content)) {
          event.result = { ...event.result, content: [...content, { type: 'text', text: feedback }] };
        } else {
          throw new Error('Unsupported OpenCode V2 content shape; check the installed API');
        }
      } else if (event.status === 'error') {
        // V2 includes failed tool attempts; retain its error and attach lint.
        event.error = { ...event.error, message: `${event.error.message}\n[Driver lint]\n${report.slice(0, 12000)}` };
      } else {
        throw new Error('Unsupported OpenCode V2 tool status; check the installed API');
      }
    });
  },
});
