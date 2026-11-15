import { badge, button, codeBlock, esc, icon, json, link, table } from './components';
import type { Operation, Profile, Snapshot } from './types';

export const repository = 'https://github.com/mbkranz/fileroute';
const planLink = 'https://chatgpt.com/library?file_id=libfile_fde3_f-08c71fd4b1e081918cc872fcec567f12_21dd13e4785481919e12d7fb02d2eb12&account_id=personal';

export function title(text: string, description: string, action = '') {
  return `<header class="page-heading"><div><h1>${esc(text)}</h1><p>${esc(description)}</p></div>${action}</header>`;
}

export function sectionHeader(label: string, description: string, action = '') {
  return `<div class="section-heading"><div><h2>${esc(label)}</h2><p>${esc(description)}</p></div>${action}</div>`;
}

export function overview(snapshot: Snapshot, profile: Profile, operations: Operation[], local: { branch: string | null; commit: string | null } | null) {
  const branch = snapshot.repository;
  const upstreamTests = snapshot.evidence.find(item => item.id === 'base' && item.status === 'Passed locally')?.detail.match(/\d+/)?.[0] ?? '—';
  const figures = [
    ['terminal', Object.keys(profile.cli).length, 'CLI commands', 'Mapped leaf commands', 'olive', '#verification'],
    ['braces', operations.length, 'Schema-driven forms', `Draft 2020-12 · v${profile.version}`, 'blue', '#schemas'],
    ['flask-conical', upstreamTests, 'Baseline tests', 'Passed locally · pinned checkout', 'violet', '#verification'],
    ['shield-check', snapshot.gates.find(item => item.id === 'live')?.status === 'Passed' ? 'Passed' : 'Gated', 'Live acceptance', 'Explicit approval per operation', 'amber', '#verification'],
  ];
  return `
    ${title('Workflow overview', 'Browse catalogs, preview operations, and track CLI parity.', button('Update workflow', 'edit-workflow', 'pencil'))}
    <section class="hero" aria-labelledby="next-title">
      <div class="hero-copy">
        <span class="hero-label"><span></span> Next verification</span>
        <h2 id="next-title">${esc(snapshot.next.title)}</h2>
        <p>${esc(snapshot.next.description)}</p>
        <div class="hero-actions">
          <a class="btn bright" href="#catalog?view=graph">${icon('network')} Open catalog explorer ${icon('arrow-right')}</a>
          ${button('Copy CLI commands', 'copy-next', 'copy', 'dark')}
        </div>
      </div>
      <div class="hero-routes" aria-label="Pilot routes: SharePoint mapping download and union schema publish">
        <div class="route-column">
          <div class="route-node"><span class="route-icon">${icon('globe')}</span><div><span>Remote source</span><strong>Mapping workbook</strong></div></div>
          <div class="route-node"><span class="route-icon">${icon('file-text')}</span><div><span>Staged output</span><strong>Survey union schema</strong></div></div>
        </div>
        <div class="route-links"><div><em>Pull</em><span></span>${icon('chevron-right')}</div><div><em>Push</em><span></span>${icon('chevron-right')}</div></div>
        <div class="route-column compact"><div class="route-end" title="Local file">${icon('hard-drive')}</div><div class="route-end" title="Remote target">${icon('upload')}</div></div>
      </div>
    </section>
    <div class="metric-grid">${figures.map(([glyph, value, label, detail, tone, href]) => `
      <a href="${href}" class="metric card"><span class="metric-icon ${tone}">${icon(String(glyph))}</span>
        <span class="metric-arrow">${icon('arrow-up-right')}</span><strong>${esc(value)}</strong><span class="metric-label">${esc(label)}</span><span class="metric-detail">${esc(detail)}</span>
      </a>`).join('')}</div>
    <div class="overview-grid">
      <section class="card parity-card">
        ${sectionHeader('CLI parity', 'Operation coverage and remaining checks.', link('View full matrix', '#verification'))}
        ${table(['Workflow', 'CLI', 'Control center'], snapshot.parity.map(row => [
          `<a class="row-link" href="#${esc(row.id)}">${icon(parityIcons[row.id] ?? 'terminal')}${esc(row.label)}</a>`,
          `<code class="subtle-code">${esc(row.cli)}</code>`, badge(row.local, row.local === 'Approval gate' ? 'amber' : row.local === 'Offline preview' ? 'green' : 'quiet'),
        ]), 'CLI parity')}
      </section>
      <div class="overview-rail">
        <section class="card scope-card">
          ${sectionHeader('Verification status', 'Fileroute acceptance gates.')}
          <div class="scope-list">
            ${scopeRow('flask-conical', 'Local baseline', 'Pinned Python and CLI', snapshot.evidence.find(item => item.id === 'base')?.status ?? 'Pending')}
            ${scopeRow('git-branch', 'Hosted CI', 'Upstream implementation checks', snapshot.gates.find(item => item.id === 'ci')?.status ?? 'Pending')}
            ${scopeRow('box', 'Package and merge', 'Wheel, sdist, and upstream PR', combinedStatus(snapshot))}
            ${scopeRow('lock-keyhole', 'Live providers', 'Auth and transfer acceptance', snapshot.gates.find(item => item.id === 'live')?.status ?? 'Approval required')}
          </div>
          <div class="card-foot">${link('Open verification', '#verification', 'arrow-right')}</div>
        </section>
        <section class="card repository-card">
          ${sectionHeader('Repository snapshot', `Checked ${formatDate(snapshot.updated)}`, link('Repository', repository))}
          <dl><div><dt>Integration branch</dt><dd><code>${esc(branch.integration)}</code></dd></div>
          <div><dt>Branch head</dt><dd>${commit(branch.commit)}</dd></div>
          <div><dt>Parity baseline</dt><dd>${commit(branch.baseline)}</dd></div>
          <div><dt>Default branch</dt><dd>${commit(branch.main, 'main')}</dd></div></dl>
          <p class="hint-line">${icon('git-branch')}${esc(branch.implementation)}</p>
          ${local ? `<p class="hint-line">${icon('git-commit-horizontal')}Local checkout · ${esc(local.branch ?? 'Detached or unavailable')} · <code>${esc(local.commit?.slice(0, 7) ?? 'No commit')}</code></p>` : ''}
        </section>
      </div>
    </div>`;
}

const parityIcons: Record<string, string> = { catalog: 'folder-tree', descriptors: 'file-pen-line', discovery: 'search',
  connections: 'lock-keyhole', transfers: 'arrow-down-up', diagrams: 'network', migration: 'files',
  execution: 'activity', 'cli-setup': 'terminal' };

function combinedStatus(snapshot: Snapshot) {
  const statuses = snapshot.gates.filter(item => ['package', 'merge'].includes(item.id)).map(item => item.status);
  return statuses.length && statuses.every(status => status === 'Passed') ? 'Passed' : 'Pending';
}

function scopeRow(glyph: string, label: string, description: string, status: string) {
  return `<div class="scope-row"><span class="scope-icon">${icon(glyph)}</span><div><strong>${esc(label)}</strong><small>${esc(description)}</small></div>${badge(status)}</div>`;
}

export function commit(sha: string, prefix = '') {
  if (!/^[a-f\d]{7,40}$/i.test(sha)) return `<code>${esc(sha)}</code>`;
  return `<a class="commit-link" href="${repository}/commit/${sha}" target="_blank" rel="noopener noreferrer" title="${esc(sha)}"><code>${prefix ? esc(prefix) + ' · ' : ''}${sha.slice(0, 7)}</code>${icon('arrow-up-right')}</a>`;
}

function formatDate(value: string) {
  const date = new Date(value + 'T12:00:00-06:00');
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'America/Chicago' }).format(date);
}

export function implementation(snapshot: Snapshot) {
  return `${title('Implementation plan', 'Track the work from pilot previews through package acceptance.', link('Open source plan', planLink, 'arrow-up-right', 'btn secondary'))}
  <div class="plan-layout">
    <section class="card plan-card">${sectionHeader('Implementation tasks', 'Update status as each increment is verified.', button('Update tasks', 'edit-tasks', 'pencil'))}
      <div class="task-list">${snapshot.tasks.map(item => `<article class="task">
        <div class="task-icon">${icon(item.status === 'Passed' ? 'check' : 'list-checks')}</div>
        <div><h3>${esc(item.title)}</h3><p>${esc(item.detail)}</p><div class="task-acceptance"><strong>Acceptance</strong><span>${esc(item.acceptance)}</span></div></div>
        ${badge(item.status)}
      </article>`).join('')}</div></section>
    <section class="card plan-card">${sectionHeader('Design decisions', 'Open issues and design choices.', button('Update decisions', 'edit-decisions', 'pencil'))}
      <div class="decision-list">${snapshot.decisions.map(row => `<article><div><h3>${esc(row.title)}</h3>${badge(row.status)}</div>
        <p>${esc(row.detail)}</p>${row.link.startsWith('https://') ? link('Open reference', row.link) : ''}</article>`).join('')}</div>
    </section>
  </div>`;
}

export function architecture(isLocal: boolean, state: { branch: string | null; commit: string | null; workingRoot: string } | null) {
  return `${title('Architecture', 'Frontend, local server, and the authoritative Python operations.')}
  <section class="card architecture-card">${sectionHeader('Runtime boundary', 'The browser submits typed arguments and renders returned results.')}
    <div class="architecture-flow">
      <article>${icon('code-xml')}<h3>Vite frontend</h3><p>Vanilla TypeScript, Web Awesome tree and split panel, and schema validation with Ajv 2020.</p><code>fileroute/ui/static/</code></article>
      <span>${icon('arrow-right')}</span>
      <article>${icon('shield-check')}<h3>Local FastAPI</h3><p>Loopback, session validation, reviewed scope, and in-memory job status.</p><code>control_center/app.py</code></article>
      <span>${icon('arrow-right')}</span>
      <article>${icon('terminal')}<h3>Fileroute Python and CLI</h3><p>Descriptor origin, resolution, graph, transfer planning, provider clients, and Typer.</p><code>mbkranz/fileroute</code></article>
    </div>
  </section>
  <section class="card">${sectionHeader('Offline and live behavior', isLocal ? 'Local FastAPI session is active.' : 'Hosted fixture mode is active.')}
    ${table(['Workflow', 'Offline and local', 'Live acceptance'], [
      ['Catalog, editing, and diagrams', 'Inspect files, generate exports, and preview origin-aware edits.', 'Requires a local write confirmation where applicable.'],
      ['Resolution and discovery', 'Infer locators and review remote roots and templates.', 'Metadata checks and listing require explicit approval.'],
      ['Authentication', 'Inspect profile names, settings, and credential references.', 'Checks and consent run through local provider tooling after approval.'],
      ['Transfers and jobs', 'Plan locally, inspect routes, and copy CLI dry runs.', 'Review and approve the exact plan; re-plan before dispatch.'],
    ], 'Offline and live behavior')}
  </section>
  <div class="plan-layout">
    <section class="card plan-card">
      ${sectionHeader('Run the local workbench', 'The download includes built assets. Start with the fixtures, then set your project as the working root.')}
      <div class="panel-body">
        ${codeBlock('uv venv\nuv pip install -r requirements-local.txt\nuv run --no-project python -m control_center.app catalog.yaml --root public/fixtures --no-open')}
        <div class="actions">${link('Download local workbench', '/downloads/fileroute-local.zip', 'arrow-down-to-line', 'btn primary')}</div>
        <p class="muted">The upstream plan adds <code>fileroute ui</code> and an optional <code>ui</code> package extra. This workbench launches from its own source checkout.</p>
      </div>
    </section>
    <section class="card plan-card">${sectionHeader('Current local checkout', 'Git is read from the local working root.')}
      <div class="panel-body">${state ? `<dl class="detail-band"><div><dt>Branch</dt><dd>${esc(state.branch ?? 'Detached HEAD or missing Git metadata')}</dd></div>
        <div><dt>Commit</dt><dd><code>${esc(state.commit ?? 'Unavailable')}</code></dd></div>
        <div><dt>Working root</dt><dd><code>${esc(state.workingRoot)}</code></dd></div></dl>`
        : '<p class="empty">Open the local workbench to inspect your checkout. The overview shows the recorded GitHub snapshot.</p>'}</div>
    </section>
  </div>`;
}

export function verification(snapshot: Snapshot, profile: Profile) {
  return `${title('Verification', 'Track local test evidence, hosted CI, distribution, merge, and live acceptance.', button('Update evidence', 'edit-evidence', 'pencil'))}
  <section class="card verification-next">
    ${sectionHeader(snapshot.next.title, snapshot.next.description, button('Copy CLI commands', 'copy-next', 'copy'))}
    <div class="panel-body">${codeBlock(snapshot.next.commands.join('\n'))}</div>
  </section>
  <section class="card">${sectionHeader('Feature parity matrix', 'Scope and outstanding work by CLI workflow.', button('Update parity', 'edit-parity', 'pencil'))}
    ${table(['Workflow', 'CLI', 'Control center', 'Remaining verification'], snapshot.parity.map(item => [
      `<a class="row-link" href="#${esc(item.id)}">${esc(item.label)}</a>`, `<code>${esc(item.cli)}</code>`, badge(item.local, 'quiet'), esc(item.gate)
    ]), 'Feature parity matrix')}
  </section>
  <section class="card">${sectionHeader('Registered CLI commands', 'Leaf command mappings for the pinned Typer CLI.')}
    ${table(['CLI command', 'Operation form'], Object.entries(profile.cli).map(([command, id]) => [
      `<code>fileroute ${esc(command)}</code>`, `<a class="row-link" href="#schemas?operation=${encodeURIComponent(id)}">${esc(id)}</a>`,
    ]), 'Registered CLI commands')}
  </section>
  <section class="card">${sectionHeader('Test and release evidence', 'Record the checkout, command, and result with each entry.')}
    ${table(['Verification', 'Status', 'Evidence'], snapshot.evidence.map(item => [
      `<strong>${esc(item.layer)}</strong>`, badge(item.status), `${esc(item.detail)}${item.command ? `<details class="evidence-command"><summary>Command</summary>${codeBlock(item.command)}</details>` : ''}`
    ]), 'Test and release evidence')}
  </section>
  <section class="card">${sectionHeader('Acceptance gates', 'Each gate requires its own evidence.', button('Update gates', 'edit-gates', 'pencil'))}
    ${table(['Gate', 'Status', 'Acceptance'], snapshot.gates.map(item => [esc(item.title), badge(item.status), esc(item.detail)]), 'Acceptance gates')}
  </section>`;
}

export function schemas(profile: Profile, operations: Operation[], snapshot: Snapshot, selected: string, selection: unknown) {
  const files = [
    ['UI profile', 'Versioned profile and CLI mapping', '/schemas/ui-profile.json'],
    ['Operations', 'Pydantic-backed function arguments', profile.operations],
    ['Catalog', 'Catalog, resource, link, and location models', profile.catalog],
    ['Authentication', 'Profiles and provider-specific modes', profile.auth],
  ];
  const review = new Map(snapshot.schemaReview.map(item => [item.id, item]));
  return `${title('Schema registry', 'Inspect the generated argument contracts used by the workbench.', button('Update schema coverage', 'edit-schemaReview', 'pencil'))}
    <section class="card schema-artifacts">${sectionHeader('Generated artifacts', `Draft 2020-12 · UI schema profile v${profile.version}`)}
      <div class="artifact-list">${files.map(([label, detail, path]) => `<a href="${esc(path)}" download class="artifact"><span class="metric-icon olive">${icon('braces')}</span>
        <span><strong>${esc(label)}</strong><small>${esc(detail)}</small><code>${esc(path.split('/').at(-1))}</code></span>${icon('arrow-down-to-line')}</a>`).join('')}</div>
    </section>
    <div class="plan-layout schema-layout">
      <section class="card">${sectionHeader('Operation coverage', `${operations.length} operation forms generated from the profile.`)}
        ${table(['Operation', 'Review'], operations.map(item => [
          `<button type="button" class="schema-pick ${item.id === selected ? 'active' : ''}" data-schema="${esc(item.id)}" aria-pressed="${item.id === selected}"><strong>${esc(item.label)}</strong><code>${esc(item.id)}</code></button>`,
          badge(review.get(item.id)?.status ?? 'Generated', review.has(item.id) ? '' : 'quiet')
        ]), 'Operation schema coverage')}
      </section>
      <section class="card schema-inspector">${sectionHeader(selected, review.get(selected)?.detail ?? 'Argument contract · Pydantic validation mode', button('Copy schema', 'copy-schema', 'copy'))}
        <div class="panel-body">${codeBlock(json(selection))}</div>
      </section>
    </div>`;
}
