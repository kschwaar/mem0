import { copyFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'tsup';

const configDir = dirname(fileURLToPath(import.meta.url));
const _require = createRequire(import.meta.url);
const pkg = _require('./package.json');

export default defineConfig({
  entry: ['src/index.ts'],
  format: ['esm'],
  dts: true,
  clean: true,
  define: {
    __CLI_VERSION__: JSON.stringify(pkg.version),
  },
  onSuccess: () => {
    copyFileSync(
      resolve(configDir, '../cli-spec.json'),
      resolve(configDir, 'dist/cli-spec.json'),
    );
  },
});

