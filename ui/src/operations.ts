import { badge, button, codeBlock, esc, icon, isRecord, json, table } from './components';
import { title, sectionHeader } from './project';
import type { FormRenderer } from './forms';
import type { Operation, RecordValue, Result } from './types';

export const sections: Record<string, { title: string; description: string; glyph: string }> = {
  overview: { title: 'Workflow overview', description: '', glyph: 'layout-dashboard' },
  catalog: { title: 'Catalog explorer', description: '', glyph: 'folder-tree' },
  descriptors: { title: 'Descriptors', description: 'Add entries, edit authored metadata, and resolve locations.', glyph: 'file-pen-line' },
  discovery: { title: 'Remote discovery', description: 'Preview template scope, review matches, and save a concrete catalog.', glyph: 'search' },
  connections: { title: 'Connections', description: 'Inspect profiles and review provider authentication requests.', glyph: 'lock-keyhole' },
  transfers: { title: 'Transfer planning', description: 'Review exact routes and compare against Fileroute CLI dry runs.', glyph: 'arrow-down-up' },
  diagrams: { title: 'Diagrams', description: 'Inspect relationships between sources, artifacts, and targets.', glyph: 'network' },
  migration: { title: 'Migration and cloning', description: 'Review legacy catalog conversion and descriptor copies.', glyph: 'files' },
  execution: { title: 'Execution status', description: 'Inspect local jobs and review pending operations.', glyph: 'activity' },
  'cli-setup': { title: 'CLI setup', description: 'Inspect CLI help and generate shell completion scripts.', glyph: 'terminal' },
  plan: { title: 'Implementation plan', description: '', glyph: 'list-checks' },
  architecture: { title: 'Architecture', description: '', glyph: 'network' },
  schemas: { title: 'Schema registry', description: '', glyph: 'braces' },
  verification: { title: 'Verification', description: '', glyph: 'flask-conical' },
};

function operationTabs(operations: Operation[], selected: string) {
  return `<div class="operation-tabs" aria-label="Choose an operation">${operations.map(item => `<button type="button"
    data-operation="${esc(item.id)}" class="${selected === item.id ? 'active' : ''}" aria-pressed="${selected === item.id}">${esc(item.label)}</button>`).join('')}</div>`;
}

function starter(view: string, local: boolean) {
  if (local) return '';
  const example: Record<string, string> = {
    descriptors: `${button('Edit union schema', 'sample-edit', 'pencil')}${button('Resolve mapping', 'sample-resolve', 'route')}`,
    discovery: button('Use survey releases fixture', 'sample-discovery', 'folder'),
    connections: `${button('List fixture profiles', 'sample-profiles', 'lock-keyhole')}${button('Configure a profile', 'sample-profile', 'plus')}`,
    transfers: `${button('Mapping download', 'sample-pull', 'arrow-down')}${button('Union-schema publish', 'sample-push', 'arrow-up')}`,
    diagrams: button('Use catalog fixture', 'sample-diagram', 'network'),
    migration: `${button('Preview legacy migration', 'sample-migrate', 'files')}${button('Preview descriptor clone', 'sample-clone', 'copy')}`,
    'cli-setup': button('Bash completion', 'sample-completion', 'terminal'),
  };
  return example[view] ? `<div class="examples"><span>Fixture shortcuts</span>${example[view]}</div>` : '';
}

export function operationView(
  view: string, operations: Operation[], current: Operation, form: FormRenderer, values: RecordValue,
  currentResult: Result | null, errors: string[], local: boolean, jobs: Result[],
) {
  const section = sections[view];
  return `${title(section.title, section.description, view === 'execution' ? button('Refresh status', 'refresh-jobs', 'refresh-ccw') : '')}
    ${view === 'execution' ? jobList(jobs, local) : starter(view, local)}
    ${operationTabs(operations, current.id)}
    <div class="operation-grid">
      <section class="card operation-form">
        ${sectionHeader(current.label, current.summary, current.cli ? `<code class="cli-chip">fileroute ${esc(current.cli)}</code>` : '')}
        <form data-operation-form="${esc(current.id)}" novalidate>
          <div class="field-grid">${form.render(current, values)}</div>
          <div class="form-errors" role="alert" ${errors.length ? '' : 'hidden'}>${errors.length ? `<ul>${errors.map(error => `<li>${esc(error)}</li>`).join('')}</ul>` : ''}</div>
          ${form.actions(current, local)}
        </form>
      </section>
      <section class="card result-card" aria-live="polite">
        ${renderResult(currentResult, section.glyph, local)}
      </section>
    </div>`;
}

function jobList(jobs: Result[], local: boolean) {
  return `<section class="card jobs-card">
    ${sectionHeader(local ? 'Local operation jobs' : 'Saved browser drafts', local ? 'Jobs retain their original operation and scope.' : 'Drafts can be copied into a local session for review.')}
    ${jobs.length ? table(['Operation', 'Job ID', 'Status'], jobs.map(row => [
      `<button type="button" data-job="${esc(row.job_id)}" class="row-link">${icon('activity')}${esc(row.operation)}</button>`,
      `<code>${esc(row.job_id)}</code>`, badge(String(row.status)),
    ]), 'Execution jobs') : `<p class="empty">${local ? 'Jobs will appear after an approved operation starts.' : 'No drafts saved in this browser.'}</p>`}
    ${!local && jobs.length ? `<div class="card-foot">${button('Clear drafts', 'clear-drafts', 'x', 'ghost')}</div>` : ''}
  </section>`;
}

function transfer(result: Result, local: boolean) {
  const columns = ['From', 'To', 'Provider', 'Profile'];
  const rows = (result.rows ?? []).map(row => [
    `<code>${esc(row.from)}</code>`, `<code>${esc(row.to)}</code>
      ${row.directory ? '<small>Directory · files enumerated during transfer</small>' : ''}
      ${row.localState && String(result.title).startsWith('Download') ? '<small class="attention">Local destination exists</small>' : ''}`,
    esc(row.provider), esc(row.profile),
  ]);
  return `<div class="panel-body"><div class="result-context">${badge(local ? 'Local planner' : 'Offline fixture', 'green')}${badge('Remote state and permissions unverified', 'amber')}</div></div>
    ${table(columns, rows, 'Planned transfer routes')}
    <div class="panel-body command-context"><span class="eyeline">CLI dry run</span>${codeBlock(result.command)}
      <span class="muted">Working root · <code>${esc(result.workingRoot)}</code></span>
      <div class="actions">${button('Copy CLI command', 'copy-command', 'copy')}${result.preview_id ? button('Review execution', 'review-transfer', 'shield-check') : ''}</div></div>`;
}

function diff(result: Result) {
  if (typeof result.patch === 'string') {
    return `<div class="panel-body"><div class="result-context"><code>${esc(result.origin)}</code></div>
      ${renderPatch(result.patch)}${result.command ? commandBlock(result) : ''}</div>`;
  }
  const before = isRecord(result.before) ? result.before : {};
  const after = isRecord(result.after) ? result.after : {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter(key => json(before[key]) !== json(after[key]));
  return `<div class="panel-body"><div class="result-context">${result.verification ? badge(String(result.verification), 'green') : ''}<code>${esc(result.origin)}</code></div>
    ${keys.length ? `<div class="diff-list">${keys.map(key => `<article><strong>${esc(key)}</strong><div class="diff-pair"><div><span>Authored</span>
      ${codeBlock(Object.hasOwn(before, key) ? json(before[key]) : '(omitted)')}</div><div><span>Proposed</span>
      ${codeBlock(Object.hasOwn(after, key) ? json(after[key]) : '(omitted)')}</div></div></article>`).join('')}</div>`
    : '<p class="empty">The preview has no field changes.</p>'}
    ${result.revision ? '<p class="muted">The preview records the physical descriptor revision.</p>' : ''}</div>`;
}

export function renderPatch(patch: string) {
  return patch ? `<pre class="yaml-diff" aria-label="Unified descriptor diff"><code>${patch.trimEnd().split('\n').map(line => {
    const tone = line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@') ? 'header'
      : line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : 'context';
    return `<span class="${tone}">${esc(line)}</span>`;
  }).join('\n')}</code></pre>` : '<p class="empty">No file changes.</p>';
}

function commandBlock(result: Result) {
  return `<div class="command-context"><span class="eyeline">CLI command</span>${codeBlock(result.command)}
    ${result.workingRoot ? `<span class="muted">Working root · <code>${esc(result.workingRoot)}</code></span>` : ''}
    <div class="actions">${button('Copy CLI command', 'copy-command', 'copy')}</div></div>`;
}

function diagram(result: Result) {
  const frame = result.format === 'svg'
    ? `<img class="diagram-image" alt="Descriptor graph" src="data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.content ?? '')}" />`
    : result.format === 'html' ? `<iframe sandbox="allow-scripts" class="diagram-frame" title="Fileroute descriptor dictionary"
      srcdoc="${esc(result.content)}"></iframe>` : codeBlock(result.content);
  return `<div class="panel-body"><div class="result-context">${badge(`${result.nodes} nodes · ${result.edges} edges`, 'quiet')}
    ${button('Download diagram', 'download-result', 'arrow-down-to-line')}${result.companion ? button('Download companion SVG', 'download-companion', 'arrow-down-to-line') : ''}</div>${frame}</div>`;
}

function rowsResult(result: Result) {
  const headers: Record<string, string[]> = {
    discovery: ['root', 'template', 'local', 'provider', 'profile'],
    matches: ['remote', 'local', 'captures'],
    'transfer-result': ['from', 'to', 'provider', 'profile'],
    profiles: ['name', 'provider', 'mode'], migration: ['source', 'output', 'registeredNames', 'links'],
  };
  const keys = headers[result.kind ?? ''] ?? Object.keys(result.rows?.[0] ?? {});
  return `${table(keys.map(key => key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, char => char.toUpperCase())),
    (result.rows ?? []).map(row => keys.map(key => `<code>${esc(typeof row[key] === 'object' ? json(row[key]) : row[key])}</code>`)),
    String(result.title ?? 'Result'))}
    ${result.content || result.result_id ? `<div class="panel-body">${result.content ? codeBlock(result.content) : ''}
      ${result.result_id ? button('Review save', 'review-discovery-save', 'files') : ''}</div>` : ''}`;
}

export function renderResult(result: Result | null, glyph: string, local: boolean) {
  if (!result) return `<div class="empty-result"><span class="metric-icon olive">${icon(glyph)}</span><h2>Operation results</h2>
    <p>Submit the form to review a preview or request.</p></div>`;
  const heading = sectionHeader(String(result.title ?? 'Operation result'), '', result.status ? badge(String(result.status)) : '');
  let body: string;
  switch (result.kind) {
    case 'auth-description': {
      const routes = result.routes as RecordValue[];
      const guides = result.guides as RecordValue[];
      body = `<div class="panel-body">
        ${routes.map(route => `<div class="resource-row"><dt>${esc(route.provider ?? 'Unknown provider')}</dt><dd>
          <code>${esc(route.url)}</code><p>${esc(route.profile ?? 'Provider defaults')} · ${esc(String(route.profile_source).replaceAll('_', ' '))}
          ${route.mode ? ' · ' + esc(route.mode) : ''}</p>
          ${route.origin_descriptor ? `<p>Declared in <code>${esc(route.origin_descriptor)}</code> at <code>${esc(route.origin_selector)}</code>${route.inherited ? ' · inherited' : ''}</p>` : ''}
          ${route.error ? `<p class="attention">${esc(route.error)}</p>` : ''}
          ${local && route.profile && !route.error ? `<div class="actions">
            <button type="button" class="btn secondary" data-auth-next="check" data-profile="${esc(route.profile)}" data-url="${esc(route.check_url)}">${route.check_url ? 'Check read access' : 'Check credentials'}</button>
            ${['delegated', 'user_oauth'].includes(String(route.mode)) ? `<button type="button" class="btn secondary" data-auth-next="login" data-profile="${esc(route.profile)}" data-provider="${route.provider === 'SharePoint' ? 'sharepoint' : 'gdrive'}">Sign in</button>` : ''}
          </div>` : ''}</dd></div>`).join('')}
        ${guides.map(guide => `<details class="evidence-command"><summary>${esc(guide.title)} · ${esc(guide.mode)}</summary>
          ${(guide.required_settings as string[]).length ? `<p>Required settings · ${esc((guide.required_settings as string[]).join(', '))}</p>` : ''}
          <ul>${(guide.next_steps as string[]).map(step => `<li><code>${esc(step)}</code></li>`).join('')}</ul>
          <button type="button" class="btn secondary" data-auth-next="configure" data-provider="${esc(guide.provider)}" data-mode="${esc(guide.mode)}">Configure profile</button></details>`).join('')}
        ${!routes.length && !guides.length ? '<p>No remote locations apply to this scope.</p>' : ''}</div>`;
      break;
    }
    case 'transfer': body = transfer(result, local); break;
    case 'diff': body = diff(result); break;
    case 'diagram': body = diagram(result); break;
    case 'profiles':
    case 'discovery':
    case 'matches':
    case 'migration':
    case 'transfer-result': body = rowsResult(result); break;
    case 'job': {
      const { job_id, operation, scope, status, error, result: jobResult } = result;
      const finish = isRecord(jobResult) ? jobResult as Result : null;
      body = `<div class="panel-body"><dl class="detail-band"><div><dt>Job ID</dt><dd><code>${esc(job_id)}</code></dd></div>
        <div><dt>Operation</dt><dd><code>${esc(operation)}</code></dd></div><div><dt>Status</dt><dd>${esc(status)}</dd></div></dl>
        ${error ? `<p class="attention">${esc(error)}</p>` : ''}${scope ? `<details class="evidence-command"><summary>Operation scope</summary>${codeBlock(scope)}</details>` : ''}
        <div class="actions">${button('Copy request', 'copy-job', 'copy')}${result.catalogScope ? button('Open originating entry', 'open-job-entry', 'network') : ''}</div></div>
        ${finish ? renderResult(finish, glyph, local) : ''}`;
      break;
    }
    default: {
      const { kind: _kind, title: _title, status: _status, content, ...details } = result;
      body = `<div class="panel-body">${content ? codeBlock(content) : Object.keys(details).length ? codeBlock(details) : ''}
        ${content && result.kind === 'export' ? `<div class="actions">${button('Copy output', 'copy-result', 'copy')}${button('Download output', 'download-result', 'arrow-down-to-line')}</div>` : ''}</div>`;
    }
  }
  if (result.command && result.kind !== 'transfer' && !(result.kind === 'diff' && typeof result.patch === 'string')) {
    body += `<div class="panel-body">${commandBlock(result)}</div>`;
  }
  return heading + body;
}
