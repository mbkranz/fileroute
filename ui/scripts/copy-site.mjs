import { cpSync, mkdirSync, rmSync } from 'node:fs';

const localOutput = 'fileroute/ui/static';
const packages = {
  'ajv': 'LICENSE',
  'diff': 'LICENSE',
  'yaml': 'LICENSE',
  'lucide': 'LICENSE',
  '@awesome.me/webawesome': 'LICENSE.md',
  '@fontsource-variable/inter': 'LICENSE',
  '@fontsource/ibm-plex-mono': 'LICENSE',
  'lit': 'LICENSE',
  'lit-html': 'LICENSE',
  'lit-element': 'LICENSE',
  '@lit/reactive-element': 'LICENSE',
  '@floating-ui/core': 'LICENSE',
  '@floating-ui/dom': 'LICENSE',
  '@floating-ui/utils': 'LICENSE',
  'fast-deep-equal': 'LICENSE',
  'fast-uri': 'LICENSE',
  'json-schema-traverse': 'LICENSE',
  'require-from-string': 'license',
};
mkdirSync(`${localOutput}/licenses`, { recursive: true });
cpSync('LICENSE.fileroute', `${localOutput}/licenses/fileroute.txt`);
for (const [name, filename] of Object.entries(packages)) {
  cpSync(`node_modules/${name}/${filename}`, `${localOutput}/licenses/${name.split('/').at(-1)}.txt`);
}
rmSync('dist', { force: true, recursive: true });
cpSync(localOutput, 'dist', { recursive: true });
