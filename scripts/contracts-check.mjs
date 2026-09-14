import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const expectedFlag = process.argv.indexOf('--expected-dir');
const expected = path.resolve(
  root,
  expectedFlag >= 0 && process.argv[expectedFlag + 1] ? process.argv[expectedFlag + 1] : 'packages/contracts',
);
const generated = mkdtempSync(path.join(tmpdir(), 'ergou-contracts-'));
const openapiTypescript = path.join(root, 'node_modules', 'openapi-typescript', 'bin', 'cli.js');

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

try {
  const openapi = path.join(generated, 'openapi.json');
  const api = path.join(generated, 'api.ts');
  run('uv', [
    'run',
    '--project',
    'apps/services',
    'python',
    '-m',
    'ergou.export_schema',
    '--output',
    openapi,
  ]);
  run(process.execPath, [openapiTypescript, openapi, '-o', api]);
  const differences = [
    ['OpenAPI', path.join(expected, 'openapi.json'), openapi],
    ['TypeScript API', path.join(expected, 'src', 'api.ts'), api],
  ].filter(([, committed, current]) => readFileSync(committed).compare(readFileSync(current)) !== 0);
  if (differences.length) {
    for (const [label] of differences) console.error(`${label} 契约不是最新版本，请运行 pnpm contracts。`);
    process.exitCode = 1;
  } else {
    console.log('API 契约与服务端模型一致。');
  }
} finally {
  rmSync(generated, { recursive: true, force: true });
}
