import type { Fixture, Operation, RecordValue, Result, Snapshot } from './types';
import { json } from './components';

export const isLocal = document.querySelector<HTMLMetaElement>('meta[name="fileroute-runtime"]')?.content === 'local';
const session = document.querySelector<HTMLMetaElement>('meta[name="fileroute-session"]')?.content ?? '';

export async function get<T>(url: string, local = false): Promise<T> {
  const response = await fetch(url, { headers: local ? { 'X-Fileroute-Session': session } : undefined });
  if (!response.ok) throw new Error(`Could not load ${url} (HTTP ${response.status}).`);
  return response.json();
}

export function getStored<T>(key: string): T | null {
  try {
    const data = localStorage.getItem(key);
    return data ? JSON.parse(data) as T : null;
  } catch { return null; }
}

export function setStored(key: string, value: unknown) {
  try { localStorage.setItem(key, json(value)); }
  catch { throw new Error('Browser storage is unavailable. Export a copy to keep your edits.'); }
}

export const SNAPSHOT = 'fileroute-workflow-v1';
const QUEUE = 'fileroute-drafts-v1';

export function validSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== 'object') return false;
  const snapshot = value as Snapshot;
  const strings = (entry: Record<string, unknown>, keys: string[]) => entry && keys.every(key => typeof entry[key] === 'string');
  const rows = (items: unknown, keys: string[]) => Array.isArray(items) && items.every(item => strings(item, keys));
  return typeof snapshot.updated === 'string'
    && strings(snapshot.repository, ['integration', 'commit', 'main', 'baseline', 'implementation'])
    && strings(snapshot.next, ['title', 'description'])
    && Array.isArray(snapshot.next.commands) && snapshot.next.commands.every(value => typeof value === 'string')
    && rows(snapshot.parity, ['id', 'label', 'cli', 'local', 'gate'])
    && rows(snapshot.tasks, ['id', 'title', 'status', 'detail', 'acceptance'])
    && rows(snapshot.evidence, ['id', 'layer', 'status', 'detail', 'command'])
    && rows(snapshot.decisions, ['id', 'title', 'status', 'detail', 'link'])
    && rows(snapshot.schemaReview, ['id', 'status', 'detail'])
    && rows(snapshot.gates, ['id', 'title', 'status', 'detail']);
}

export function drafts() {
  return getStored<Result[]>(QUEUE) ?? [];
}

const writeFlags: Record<string, (args: RecordValue) => boolean> = {
  add: args => args.dry_run === false,
  update: args => args.dry_run === false,
  resolve: args => args.write === true,
  'clone.descriptor': args => args.dry_run === false,
  migrate: args => args.dry_run === false,
  diagram: args => !!args.output,
};

export function requiresApproval(operation: Operation, args: RecordValue) {
  return operation.intent === 'approval' || operation.id === 'cli.completion.install'
    || (operation.id === 'resolve' && args.online === true);
}

export function writes(operation: Operation, args: RecordValue) {
  return operation.intent === 'write' || (writeFlags[operation.id]?.(args) ?? false);
}

async function post(operation: string, args: RecordValue, approval?: string): Promise<Result> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Fileroute-Session': session };
  if (approval) headers['X-Fileroute-Approval'] = approval;
  const response = await fetch('/api/operations', {
    method: 'POST', headers, body: JSON.stringify({ operation, arguments: args }),
  });
  const result = await response.json();
  if (!response.ok) {
    const detail = result.detail;
    const message = Array.isArray(detail)
      ? detail.map(item => `${item.loc?.join('.') ?? 'Arguments'} · ${item.msg}`).join('\n')
      : detail;
    throw new Error(typeof message === 'string' ? message : 'The local operation failed.');
  }
  return result;
}

export async function runLocal(operation: string, args: RecordValue, approve = false) {
  const grant = approve ? await post('approval.request', { operation, arguments: args }) : null;
  return post(operation, args, typeof grant?.approval_id === 'string' ? grant.approval_id : undefined);
}

function shellCommand(parts: string[]) {
  return parts.map(part => /^[A-Za-z0-9_./:=@+-]+$/.test(part)
    ? part : "'" + part.replaceAll("'", "'\"'\"'") + "'").join(' ');
}

export function runFixture(fixture: Fixture, operation: Operation, args: RecordValue): Result {
  const name = operation.id;
  const selector = String(args.selector ?? '$');
  if (name === 'approval.request') return { title: 'Approval requires a local session', status: 'Approval required' };
  if (name === 'jobs.get') {
    const job = drafts().find(item => item.job_id === args.job_id);
    if (!job) throw new Error('No matching job or draft is available in this browser.');
    return job;
  }
  if (requiresApproval(operation, args) || writes(operation, args)) {
    if (name.startsWith('auth.')) {
      return { title: 'Authentication request reviewed', status: 'Local session required',
        content: 'Open the local Fileroute session to use these credential references. Hosted storage omits this request.' };
    }
    const job = { kind: 'job', job_id: `draft-${crypto.randomUUID().slice(0, 8)}`, status: 'Draft',
      title: 'Saved for local review', operation: name, scope: args, created: new Date().toISOString() };
    setStored(QUEUE, [job, ...drafts()].slice(0, 50));
    return job;
  }
  if (args.descriptor && args.descriptor !== fixture.catalog.descriptor && name !== 'migrate') {
    throw new Error('Choose catalog.yaml for the hosted fixture or open the descriptor in the local workspace.');
  }
  switch (name) {
    case 'catalog.open':
      return { title: 'Opened catalog.yaml', ...fixture.catalog };
    case 'list': {
      const rows = fixture.catalog.entries.flatMap(item => [item, ...(item.locations ?? [])]);
      const root = rows.find(row => row.selector === selector || row.pointer === selector || row.qualifiedName === selector);
      if (!root) throw new Error('Choose a known catalog selector.');
      const matches = rows.filter(row => (row.pointer === root.pointer || row.pointer.startsWith(root.pointer + '/')) &&
        (!args.kind || args.kind === 'all' || args.kind === row.kind));
      const text = matches.map(row => `${row.selector}\t${row.kind}\t${row.artifactPath ?? ''}`).join('\n');
      const command = shellCommand(['fileroute', 'list', fixture.catalog.descriptor,
        ...(args.selector ? ['--select', selector] : []),
        ...(args.kind && args.kind !== 'all' ? ['--kind', String(args.kind)] : []),
        ...(args.format === 'json' ? ['--format', 'json'] : [])]);
      return { kind: 'export', title: 'Catalog inspection', content: args.format === 'json' ? json(matches) : text,
        format: String(args.format ?? 'text'), count: matches.length, command, workingRoot: fixture.discovery.workingRoot };
    }
    case 'update': {
      const entries = fixture.catalog.entries.flatMap(item => [item, ...(item.locations ?? [])]);
      const row = entries.find(item => item.selector === selector || item.pointer === selector);
      if (!row) throw new Error('Choose a known catalog selector.');
      const before = args.kind === 'link' && row.linked ? { descriptor: row.originDescriptor } : row.entity;
      const after = { ...before, ...(args.fields as RecordValue), ...(args.extensions as RecordValue) };
      return { kind: 'diff', title: 'Edit draft', origin: row.originDescriptor, before, after, revision: row.revision, verification: 'Browser draft' };
    }
    case 'add': {
      const parent = fixture.catalog.entries.find(item => item.selector === args.parent);
      return { kind: 'diff', title: 'New entry draft', origin: parent?.originDescriptor ?? fixture.catalog.descriptor, before: {},
        after: { [String(args.name)]: { ...(args.entry as RecordValue), ...(args.extensions as RecordValue) } }, verification: 'Schema validation' };
    }
    case 'resolve': {
      const result = fixture.resolutions[selector];
      if (!result) throw new Error('Choose a known catalog, resource, or location selector.');
      return result;
    }
    case 'discovery.preview': {
      if (selector !== '$' && selector !== '$.catalogs.releases' && selector !== 'releases') throw new Error('Choose the Survey releases catalog for this fixture.');
      return { ...fixture.discovery, command: shellCommand(['fileroute', 'discover', fixture.catalog.descriptor,
        ...(args.selector ? ['--select', selector] : []), ...(args.profile ? ['--profile', String(args.profile)] : []), '--dry-run']) };
    }
    case 'transfer.preview': {
      const scope = !args.selector || selector === '$' ? '' : selector;
      const result = fixture.transfers[`${args.direction}:${scope}`];
      if (!result) throw new Error('This fixture provides mapping downloads and union-schema or catalog publish previews.');
      if (args.profile) return { ...result, command: `${String(result.command).replace(/ --dry-run$/, '')} --profile '${args.profile}' --dry-run` };
      return result;
    }
    case 'diagram':
      return fixture.diagrams[String(args.format)][String(args.detail ?? 'summary')];
    case 'migrate':
      if (args.descriptor !== 'legacy.yaml') throw new Error('Use legacy.yaml for this fixture.');
      return { ...fixture.migration, rows: fixture.migration.rows?.map(row => ({ ...row,
        output: String(row.output).replace(/^converted\//, String(args.output).replace(/\/$/, '') + '/') })) };
    case 'clone.descriptor':
      return { title: 'Clone preview', descriptor: fixture.catalog.descriptor, destination: args.target_path,
        overwrite: args.force === true, verification: 'Fixture request review' };
    case 'auth.describe': {
      if (args.profile) throw new Error('Open the local workbench to describe a custom profile.');
      const { overview, scopes, urls } = fixture.authDescriptions;
      if (args.operation) {
        const scoped = scopes[`${args.operation}:${selector}`];
        if (!scoped) throw new Error('Choose a catalog or resource in the sample.');
        if (!args.location) return scoped;
        const routes = (scoped.routes as RecordValue[]).filter(row => row.declaration_selector === args.location);
        return { ...scoped, routes, guides: (scoped.guides as RecordValue[]).filter(guide =>
          routes.some(row => row.provider === guide.provider)),
          command: `${scoped.command} ${shellCommand(['--location', String(args.location)])}` };
      }
      if (args.url) {
        const match = urls[String(args.url)];
        if (!match || !Object.hasOwn(urls, String(args.url))) throw new Error('Choose a URL from the sample or use the local workbench.');
        return match;
      }
      const providers: Record<string, string> = { s3: 'S3', gdrive: 'GoogleDrive', sharepoint: 'SharePoint' };
      const provider = providers[String(args.provider).toLowerCase()];
      if (args.provider && !provider) throw new Error('Choose s3, gdrive, or sharepoint.');
      const guides = (overview.guides as RecordValue[]).filter(guide =>
        (!provider || guide.provider === provider) && (!args.mode || args.mode === guide.mode));
      if (!guides.length) throw new Error('Choose an available authentication mode for this provider.');
      return { ...overview, guides, command: shellCommand(['fileroute', 'auth', 'describe',
        ...(args.provider ? [String(args.provider)] : []),
        ...(args.mode ? ['--mode', String(args.mode)] : [])]) };
    }
    case 'auth.profile.list':
    case 'auth.profile.show':
      if (args.config && args.config !== 'profiles.yaml') throw new Error('Use profiles.yaml for this fixture.');
      if (name === 'auth.profile.list') return fixture.profiles;
      if (!fixture.profileDetails[String(args.name)]) throw new Error('Choose a fixture profile such as archive-reader.');
      return fixture.profileDetails[String(args.name)];
    case 'cli.help':
      if (!fixture.help[String(args.command ?? '')]) throw new Error('Enter a registered command path such as auth login s3.');
      return fixture.help[String(args.command ?? '')];
    case 'cli.completion.show':
      return fixture.completion[String(args.shell)];
    default:
      throw new Error('Open a local session to perform this operation.');
  }
}
