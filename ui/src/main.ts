import '@fontsource-variable/inter/wght.css';
import '@fontsource/ibm-plex-mono/400.css';
import './style.css';

import { drafts, get, getStored, isLocal, requiresApproval, runFixture, runLocal, setStored, SNAPSHOT, validSnapshot, writes } from './api';
import { allEntries, catalogView, inspect, tree } from './catalog';
import type { CatalogMode, Filter } from './catalog';
import { graph, observeEdges } from './catalog-graph';
import { badge, button, codeBlock, download, esc, icon, isRecord, json, refreshIcons } from './components';
import { DRAFT_YAML, previewDraft, projectDraft, sampleRoutesMatch } from './descriptor-draft';
import { FormRenderer } from './forms';
import type { Schema } from './forms';
import { operationView, renderPatch, renderResult, sections } from './operations';
import { architecture, implementation, overview, schemas, verification } from './project';
import type { Catalog, CatalogEntry, DescriptorOrigin, Fixture, Operation, Profile, RecordValue, Result, Snapshot } from './types';

type LocalState = { branch: string | null; commit: string | null; workingRoot: string };
type ViewState = { id: string; args: RecordValue; result: Result | null; errors: string[]; sequence: number };
type Section = 'parity' | 'tasks' | 'evidence' | 'decisions' | 'gates' | 'schemaReview';
type EntryEdit = {
  descriptor: string; entry: CatalogEntry; origin: DescriptorOrigin; kind: CatalogEntry['kind'] | 'link';
  values: RecordValue; initial?: RecordValue; reviewed?: { args: RecordValue; result: Result };
};

const app = document.querySelector<HTMLElement>('#app')!;
const mapping = '$.resources.mapping';
const union = '$.catalogs.outputs.resources["union-schema"]';
const releases = '$.catalogs.releases';

const groups = [
  { name: 'Workspace', pages: ['overview', 'catalog', 'descriptors', 'discovery', 'connections', 'transfers', 'diagrams', 'migration', 'execution'] },
  { name: 'Engineering', pages: ['plan', 'architecture', 'schemas', 'verification', 'cli-setup'] },
];

let snapshot: Snapshot;
let original: Snapshot;
let profile: Profile;
let fixture: Fixture;
let catalog: Catalog | null = null;
let operations: Operation[];
let renderer: FormRenderer;
let page = 'catalog';
let selectedCatalog = union;
let catalogTab = 'Details';
let catalogMode: CatalogMode = 'list';
let graphFocus = union;
let graphObserver: ResizeObserver | undefined;
let catalogSearch = '';
let catalogFilter: Filter = 'all';
let schemaSelection = 'transfer.preview';
let localState: LocalState | null = null;
let jobs: Result[] = [];
let pending: (() => void | Promise<void>) | null = null;
let dialogTag = '';
let dialogEpoch = 0;
let editSection: Section | '' = '';
let draft: Snapshot | null = null;
let toastTimeout: number | undefined;
let mobileOpen = false;
let catalogBusy = false;
let catalogPreview: { descriptor: string; selector: string; operation: string; result: Result; args: RecordValue } | null = null;
let entryEdit: EntryEdit | null = null;
let draftYaml: Record<string, string> = {};
let paletteIndex = -1;
const viewState = new Map<string, ViewState>();

function makeState(id: string, args: RecordValue): ViewState {
  return { id, args, result: null, errors: [], sequence: 0 };
}

async function startup() {
  app.innerHTML = '<main class="boot"><img src="/fileroute.svg" alt="" /><p>Loading Fileroute…</p></main>';
  const [reported, uiProfile, manifest, demo] = await Promise.all([
    get<Snapshot>('/workflow.json'), get<Profile>('/schemas/ui-profile.json'), get<Operation[]>('/operation-views.json'),
    isLocal ? Promise.resolve(null) : get<Fixture>('/fixtures/workspace.json'),
  ]);
  original = reported;
  const saved = getStored<Snapshot>(SNAPSHOT);
  snapshot = validSnapshot(saved) ? saved : structuredClone(reported);
  profile = uiProfile;
  operations = manifest;
  renderer = new FormRenderer(await get<Schema>(profile.operations));
  if (demo) {
    fixture = demo;
    draftYaml = { ...demo.yaml };
    catalog = demo.catalog;
    const saved = getStored<Record<string, string>>(DRAFT_YAML);
    if (isRecord(saved)) {
      const current = { ...draftYaml, ...Object.fromEntries(Object.entries(saved).filter(([name, contents]) =>
        Object.hasOwn(demo.yaml, name) && typeof contents === 'string')) };
      try {
        catalog = projectDraft(demo.catalog, current);
        draftYaml = current;
      } catch { /* Ignore an invalid browser draft and open the published sample. */ }
    }
  } else {
    selectedCatalog = '$';
    graphFocus = '$';
    const [state, current] = await Promise.allSettled([get<LocalState>('/api/state', true), runLocal('catalog.open', {})]);
    if (state.status === 'fulfilled') localState = state.value;
    if (current.status === 'fulfilled') catalog = current.value as unknown as Catalog;
  }
  await updateJobs();
  mount();
  route(false);
}

function nav() {
  return groups.map(group => `<div class="nav-group"><h2>${esc(group.name)}</h2>${group.pages.map(key => {
    const section = sections[key];
    return `<a href="#${key}" class="nav-link ${key === page ? 'active' : ''}" ${key === page ? 'aria-current="page"' : ''}>
      ${icon(section.glyph)}<span>${esc(key === 'overview' ? 'Overview' : key === 'catalog' ? 'Catalog' : key === 'discovery' ? 'Discovery'
          : key === 'transfers' ? 'Transfers' : key === 'migration' ? 'Migration' : key === 'execution' ? 'Execution' : key === 'plan' ? 'Implementation plan' : section.title)}</span>
      ${key === page ? icon('chevron-right') : ''}</a>`;
  }).join('')}</div>`).join('');
}

function mount() {
  app.innerHTML = `<div class="shell">
    <aside class="sidebar" aria-label="Fileroute navigation">
      <a class="brand" href="#overview"><img src="/fileroute.svg" alt="" width="37" height="37" /><div><strong>Fileroute</strong><span>Control center</span></div></a>
      <nav id="nav">${nav()}</nav>
      <div class="sidebar-foot"><div class="runtime-card"><span class="runtime-icon">${icon(isLocal ? 'shield-check' : 'unplug')}</span>
      <div><strong>${isLocal ? 'Local session' : 'Offline mode'}</strong><small>Provider actions gated</small></div>
      <span class="runtime-dot"></span></div></div>
    </aside>
    <button type="button" class="menu-overlay" data-action="close-menu" aria-label="Close navigation" hidden></button>
    <div class="workspace"><header class="topbar">
      <div class="breadcrumbs"><button type="button" class="icon-btn menu-toggle" data-action="toggle-menu" aria-label="Open navigation">${icon('menu')}</button>
        <span>Workspace</span>${icon('chevron-right')}<strong id="breadcrumb"></strong></div>
      <div class="top-actions">
        <button type="button" class="search-trigger" data-action="search">${icon('search')}<span>Search workspace</span><kbd>⌘ K</kbd></button>
        <button type="button" class="icon-btn top-help" data-action="workspace-help" aria-label="Workspace help">${icon('circle-help')}</button>
      </div>
    </header><main id="view" tabindex="-1"></main>
    <footer class="workspace-footer"><span>Fileroute Control Center</span><span>${isLocal ? 'Local FastAPI session' : 'Offline fixtures'} <b>·</b> Draft 2020-12 profiles</span></footer></div>
  </div>
  <dialog id="dialog"></dialog><div id="toast" role="status" aria-live="polite" hidden></div>`;
  document.querySelector<HTMLDialogElement>('#dialog')!.addEventListener('close', event => {
    if ((event.currentTarget as HTMLDialogElement).open) return;
    pending = null;
    dialogTag = '';
    draft = null;
    editSection = '';
    entryEdit = null;
  });
  document.addEventListener('click', onClick);
  document.addEventListener('submit', onSubmit);
  document.addEventListener('change', onChange);
  document.addEventListener('input', onInput);
  document.addEventListener('keydown', event => {
    if (dialogTag === 'search' && ['ArrowDown', 'ArrowUp', 'Enter'].includes(event.key)) {
      const hits = [...document.querySelectorAll<HTMLButtonElement>('.search-hit')];
      if (hits.length && event.key !== 'Enter') {
        event.preventDefault();
        paletteIndex = (paletteIndex + (event.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length;
        hits[paletteIndex]?.focus();
      } else if (hits.length && (event.target as HTMLElement).id === 'workspace-search') {
        event.preventDefault();
        hits[Math.max(paletteIndex, 0)]?.click();
      }
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      searchDialog();
    }
  });
  window.addEventListener('hashchange', () => route(true));
  refreshIcons();
}

function route(focus: boolean) {
  const [destination, rawSearch] = (location.hash.replace(/^#/, '') || 'catalog').split('?');
  const parameters = new URLSearchParams(rawSearch);
  const selectedOp = parameters.get('operation');
  if (destination === 'schemas' && selectedOp && renderer.operations.has(selectedOp)) schemaSelection = selectedOp;
  if (destination === 'view') return;
  page = Object.hasOwn(sections, destination) ? destination : 'overview';
  if (page === 'catalog') {
    const mode = parameters.get('view');
    const selector = parameters.get('select');
    if (mode === 'list' || mode === 'graph') catalogMode = mode;
    if (selector) selectCatalog(selector, false, false);
  }
  const shell = document.querySelector('#nav')!;
  shell.innerHTML = nav();
  refreshIcons(shell as HTMLElement);
  document.querySelector('#breadcrumb')!.textContent = page === 'overview' ? 'Overview' : sections[page].title;
  document.title = page === 'overview' ? 'Fileroute Control Center' : `${sections[page].title} · Fileroute`;
  menu(false);
  render();
  if (focus) {
    window.scrollTo(0, 0);
    document.querySelector<HTMLElement>('#view')?.focus({ preventScroll: true });
  }
}

function navigate(destination: string) {
  if (location.hash === '#' + destination) route(false);
  else location.hash = destination;
}

function defaultArgs(id: string): RecordValue {
  const entry = catalog?.entries.find(item => item.selector === selectedCatalog);
  const base = catalog ? { descriptor: catalog.descriptor } : {};
  const values: Record<string, RecordValue> = {
    'catalog.open': base, list: { ...base, kind: 'all', format: 'text' }, activate: base,
    add: { ...base, kind: 'resource', dry_run: true },
    update: { ...base, selector: selectedCatalog, kind: entry?.kind ?? 'resource', dry_run: true },
    resolve: { ...base, selector: selectedCatalog, online: false, write: false },
    'discovery.preview': { ...base, ...(!isLocal ? { selector: releases } : {}) },
    'discovery.run': { ...base, ...(!isLocal ? { selector: releases } : {}), format: 'text' },
    'discovery.save': { output: 'discovered.yaml' },
    'transfer.preview': { ...base, direction: 'pull', ...(!isLocal ? { selector: mapping } : {}) },
    'transfer.run': {},
    diagram: { ...base, format: 'svg', detail: 'summary' },
    'clone.descriptor': { ...base, target_path: 'catalog-copy.yaml', dry_run: true, force: false },
    migrate: { ...(!isLocal ? { descriptor: 'legacy.yaml' } : {}), output: 'converted', dry_run: true },
    'auth.profile.list': { ...(!isLocal ? { config: 'profiles.yaml' } : {}) },
    'auth.profile.show': { ...(!isLocal ? { config: 'profiles.yaml', name: 'archive-reader' } : {}) },
    'auth.profile.save': { settings: { provider: 'GoogleDrive', mode: 'adc' } },
    'auth.login.gdrive': { method: 'saved' }, 'auth.login.microsoft': { method: 'saved' }, 'auth.login.sharepoint': { method: 'saved' },
    'auth.login.s3': { method: 'saved' },
    'cli.completion.show': { shell: 'bash' }, 'cli.completion.install': { shell: 'bash' },
  };
  return values[id] ?? {};
}

const starting: Record<string, string> = { descriptors: 'update', discovery: 'discovery.preview', connections: 'auth.describe',
  transfers: 'transfer.preview', diagrams: 'diagram', migration: 'migrate', execution: 'jobs.get', 'cli-setup': 'cli.help' };

function renderResultData(result: Result | null | undefined): Result | null {
  if (!result) return null;
  return result.kind === 'job' && isRecord(result.result) ? result.result as Result : result;
}

function initialized(view: string) {
  if (!viewState.has(view)) {
    const id = starting[view] ?? 'catalog.open';
    viewState.set(view, makeState(id, defaultArgs(id)));
  }
  return viewState.get(view)!;
}

function render() {
  graphObserver?.disconnect();
  const target = document.querySelector<HTMLElement>('#view')!;
  switch (page) {
    case 'overview': target.innerHTML = overview(snapshot, profile, operations, localState); break;
    case 'catalog': {
      target.innerHTML = catalogView(catalog, selectedCatalog, catalogTab, catalogMode, isLocal, jobs, hasYamlDraft());
      if (catalog) {
        const search = target.querySelector<HTMLInputElement>('#catalog-search')!;
        const kind = target.querySelector<HTMLSelectElement>('#catalog-kind')!;
        search.value = catalogSearch;
        kind.value = catalogFilter;
        renderCatalogBrowser();
        drawCatalogPreview();
      }
      break;
    }
    case 'plan': target.innerHTML = implementation(snapshot); break;
    case 'architecture': target.innerHTML = architecture(isLocal, localState); break;
    case 'schemas': target.innerHTML = schemas(profile, operations, snapshot, schemaSelection, renderer.schemaFor(schemaSelection)); break;
    case 'verification': target.innerHTML = verification(snapshot, profile); break;
    default: {
      const state = initialized(page);
      target.innerHTML = operationView(page, operations.filter(item => item.view === page), operation(state.id),
        renderer, state.args, state.result, state.errors, isLocal, jobs);
      if (state.id === 'auth.describe' && state.sequence === 0) {
        void submitOperation(operation(state.id), state.args, state);
      }
    }
  }
  refreshIcons(target);
}

function hasYamlDraft() {
  return !isLocal && !!fixture && Object.entries(draftYaml).some(([name, text]) => text !== fixture.yaml[name]);
}

function renderCatalogBrowser() {
  if (!catalog) return;
  const items = document.querySelector<HTMLElement>('#catalog-tree-items')!;
  graphObserver?.disconnect();
  if (catalogMode === 'graph') {
    items.innerHTML = graph(catalog, graphFocus, selectedCatalog);
    const flow = items.querySelector<HTMLElement>('.graph-flow');
    if (flow) graphObserver = observeEdges(flow);
    refreshIcons(items);
    return;
  }
  items.innerHTML = tree(catalog, catalogSearch, catalogFilter, selectedCatalog);
  // Nested Web Awesome items can reset expansion while they connect.
  for (const node of items.querySelectorAll<HTMLElement & { expanded: boolean }>('wa-tree-item[data-expand]')) {
    node.expanded = true;
  }
  items.querySelector('wa-tree')?.addEventListener('wa-selection-change', event => {
    const item = (event as CustomEvent<{ selection: HTMLElement[] }>).detail.selection[0];
    if (item?.dataset.selector) selectCatalog(item.dataset.selector, false);
  });
  items.querySelector('wa-tree')?.addEventListener('click', event => {
    const item = (event.target as HTMLElement).closest<HTMLElement>('wa-tree-item');
    if (item?.dataset.selector) selectCatalog(item.dataset.selector, false);
  });
  refreshIcons(items);
}

function selectCatalog(value: string, refreshBrowser: boolean, refreshPanel = true) {
  if (!catalog) return;
  const row = allEntries(catalog).find(entry => entry.selector === value);
  if (!row) return;
  if (selectedCatalog !== value) catalogPreview = null;
  selectedCatalog = value;
  if (row.kind !== 'location') graphFocus = value;
  else if (catalogMode === 'list' || !catalog.relationships.some(edge =>
    (edge.from === graphFocus && edge.to === value) || (edge.to === graphFocus && edge.from === value))) graphFocus = row.parent;
  if (!refreshPanel) return;
  const panel = document.querySelector<HTMLElement>('#catalog-detail');
  if (panel) {
    panel.innerHTML = inspect(catalog, selectedCatalog, catalogTab, isLocal, jobs, hasYamlDraft());
    if (refreshBrowser) renderCatalogBrowser();
    refreshIcons(panel);
    drawCatalogPreview(false);
  }
}

function operation(id: string) {
  return operations.find(item => item.id === id)!;
}

function choose(id: string, args?: RecordValue, autorun = false) {
  const item = operation(id);
  if (!item) return;
  const previous = viewState.get(item.view);
  if (previous && previous.id !== id) previous.sequence += 1;
  const state = makeState(id, args ?? defaultArgs(id));
  viewState.set(item.view, state);
  if (item.view === 'catalog') editOperation(item, state.args);
  else {
    navigate(item.view);
    if (autorun) void submitOperation(item, state.args, state);
  }
}

function editOperation(item: Operation, args: RecordValue) {
  openDialog(item.label, item.summary, `<form data-operation-form="${esc(item.id)}" novalidate>
    <div class="field-grid">${renderer.render(item, args)}</div><div class="form-errors" role="alert" hidden></div>
    ${renderer.actions(item, isLocal)}</form>`, 'operation');
}

function openDialog(title: string, description: string, content: string, tag: string, wide = false) {
  const dialog = document.querySelector<HTMLDialogElement>('#dialog')!;
  dialogEpoch += 1;
  dialogTag = tag;
  dialog.className = wide ? 'wide' : '';
  dialog.setAttribute('aria-labelledby', 'dialog-title');
  dialog.innerHTML = `<header class="dialog-header"><div><h2 id="dialog-title">${esc(title)}</h2>${description ? `<p>${esc(description)}</p>` : ''}</div>
    <button type="button" class="icon-btn close-dialog" data-action="close-dialog" aria-label="Close dialog">${icon('x')}</button></header>
    <div class="dialog-body">${content}</div>`;
  refreshIcons(dialog);
  if (!dialog.open) dialog.showModal();
}

function closeDialog() {
  const dialog = document.querySelector<HTMLDialogElement>('#dialog')!;
  if (dialog.open) {
    dialogEpoch += 1;
    dialogTag = '';
    dialog.close();
  }
}

function tell(message: string) {
  const toast = document.querySelector<HTMLElement>('#toast')!;
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimeout);
  toastTimeout = window.setTimeout(() => { toast.hidden = true; }, 3200);
}

async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    tell('Copied to clipboard.');
  } catch {
    openDialog('Copy text', 'Select and copy from this field.', `<textarea class="copy-text" readonly rows="8">${esc(value)}</textarea>`, 'copy', true);
    const area = document.querySelector<HTMLTextAreaElement>('.copy-text')!;
    area.focus();
    area.select();
  }
}

function menu(open: boolean) {
  mobileOpen = open;
  document.querySelector('.shell')?.classList.toggle('menu-open', open);
  const overlay = document.querySelector<HTMLElement>('.menu-overlay');
  if (overlay) overlay.hidden = !open;
}

function searchDialog() {
  openDialog('Search workspace', '', `<label class="search-label palette-search">${icon('search')}<input id="workspace-search"
    type="search" placeholder="Pages, operations, or catalog resources" autofocus /></label><div id="search-results"></div>`, 'search');
  searchResults('');
  document.querySelector<HTMLInputElement>('#workspace-search')?.focus();
}

function searchResults(query: string) {
  paletteIndex = -1;
  const needle = query.trim().toLowerCase();
  const pages = Object.entries(sections).map(([key, row]) => ({ name: row.title, detail: 'Page', glyph: row.glyph, attr: 'search-page', value: key }));
  const ops = operations.map(item => ({ name: item.label, detail: item.cli ? `fileroute ${item.cli}` : 'Operation',
    glyph: 'terminal', attr: 'search-operation', value: item.id }));
  const records = catalog ? catalog.entries.map(item => ({ name: item.entity.title ?? item.name ?? catalog?.descriptor ?? 'Catalog',
    detail: item.selector, glyph: item.kind === 'catalog' ? 'folder' : 'file-text', attr: 'search-catalog', value: item.selector })) : [];
  const hits = [...pages, ...ops, ...records].filter(item => (item.name + ' ' + item.detail).toLowerCase().includes(needle)).slice(0, 12);
  const results = document.querySelector<HTMLElement>('#search-results')!;
  results.innerHTML = hits.length ? hits.map(item => `<button type="button" class="search-hit" data-${item.attr}="${esc(item.value)}">${icon(item.glyph)}
    <span><strong>${esc(item.name)}</strong><small>${esc(item.detail)}</small></span>${icon('arrow-right')}</button>`).join('') : '<p class="empty">No matching pages, operations, or resources.</p>';
  refreshIcons(results);
}

const fields: Record<Section, string[]> = {
  parity: ['label', 'cli', 'local', 'gate'], tasks: ['title', 'status', 'detail', 'acceptance'],
  evidence: ['layer', 'status', 'detail', 'command'], decisions: ['title', 'status', 'detail', 'link'],
  gates: ['title', 'status', 'detail'], schemaReview: ['id', 'status', 'detail'],
};

const labels: Record<string, string> = {
  integration: 'Integration branch', commit: 'Branch head SHA', main: 'Default branch SHA', baseline: 'Parity baseline SHA',
  implementation: 'UI implementation branch or status', updated: 'Snapshot date', label: 'Workflow', local: 'Control center status',
  gate: 'Remaining verification', id: 'Operation ID', cli: 'CLI', layer: 'Verification layer',
};

function editorField(path: string, field: string, value: unknown) {
  const label = labels[field] ?? field.replace(/^./, char => char.toUpperCase());
  const long = ['description', 'detail', 'commands', 'command', 'acceptance', 'gate'].includes(field);
  const name = `data-edit-path="${esc(path)}"`;
  return `<label class="editor-field ${long ? 'full' : ''}"><span>${esc(label)}</span>${long
    ? `<textarea ${name} rows="${field === 'commands' ? 3 : 2}">${esc(Array.isArray(value) ? value.join('\n') : value)}</textarea>`
    : field === 'id' && path.startsWith('schemaReview') ? `<select ${name}>${operations.map(item => `<option value="${esc(item.id)}" ${item.id === value ? 'selected' : ''}>${esc(item.id)}</option>`).join('')}</select>`
    : `<input ${name} type="${field === 'updated' ? 'date' : 'text'}" value="${esc(value)}" />`}</label>`;
}

function startEditor(section: Section | '' = '') {
  draft = structuredClone(snapshot);
  editSection = section;
  renderEditor();
}

function renderEditor() {
  if (!draft) return;
  let content: string;
  if (!editSection) {
    const repo = Object.entries(draft.repository).map(([field, value]) => editorField(`repository.${field}`, field, value)).join('');
    const next = Object.entries(draft.next).map(([field, value]) => editorField(`next.${field}`, field, value)).join('');
    content = `<div class="editor-tools">
      ${button('Export snapshot', 'export-workflow', 'arrow-down-to-line')}
      ${button('Import snapshot', 'import-workflow', 'upload')}
      ${button('Restore published snapshot', 'reset-workflow', 'refresh-ccw', 'ghost')}
      <input type="file" id="snapshot-file" accept="application/json,.json" hidden />
    </div><div class="field-grid">${editorField('updated', 'updated', draft.updated)}</div>
      <fieldset class="form-group"><legend>Repository</legend><div class="field-grid">${repo}</div></fieldset>
      <fieldset class="form-group"><legend>Next verification</legend><div class="field-grid">${next}</div></fieldset>`;
  } else {
    const section = editSection;
    content = draft[section].map((row, index) => {
      const record = row as RecordValue;
      const name = record.title ?? record.label ?? record.layer ?? record.id;
      return `<details class="edit-item" ${index === draft![section].length - 1 && !name ? 'open' : ''}><summary>${esc(name || 'New record')}</summary>
        <div class="field-grid">${fields[section].map(field => editorField(`${section}.${index}.${field}`, field, record[field])).join('')}</div>
        ${section === 'parity' ? '' : `<button type="button" class="btn ghost" data-remove-row="${index}">${icon('x')}Remove record</button>`}</details>`;
    }).join('') + (section === 'parity' ? '' : button('Add record', 'add-row', 'plus'));
  }
  openDialog(editSection ? 'Update ' + (editSection === 'schemaReview' ? 'schema coverage' : editSection) : 'Update workflow',
    'Edits are stored in this browser. Export a snapshot to share the changes or update the published defaults.',
    `<form id="snapshot-form">${content}<div class="form-errors" role="alert" hidden></div><div class="form-actions">
      <button type="submit" class="btn primary">${icon('check')}Save changes</button>
      ${button('Cancel', 'close-dialog', undefined, 'ghost')}</div></form>`, 'editor', true);
}

function setEditField(input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement) {
  if (!draft || !input.dataset.editPath) return;
  const parts = input.dataset.editPath.split('.');
  let current: unknown = draft;
  for (const key of parts.slice(0, -1)) current = (current as RecordValue)[key];
  const key = parts.at(-1)!;
  (current as RecordValue)[key] = key === 'commands' ? input.value.split('\n').filter(Boolean) : input.value;
}

function showErrors(form: HTMLFormElement, errors: string[]) {
  const panel = form.querySelector<HTMLElement>('.form-errors');
  if (!panel) return;
  panel.innerHTML = errors.length ? `<ul>${errors.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '';
  panel.hidden = !errors.length;
}

function openEntryEditor(entry: CatalogEntry, link = false) {
  if (!catalog || (link && !entry.link)) return;
  const origin = link ? entry.link! : entry;
  entryEdit = { descriptor: catalog.descriptor, entry, origin, kind: link ? 'link' : entry.kind, values: { ...origin.entity } };
  showEntryEditor(entryEdit);
}

function showEntryEditor(edit: EntryEdit) {
  const filename = edit.origin.originDescriptor;
  const format = filename.toLowerCase().endsWith('.json') ? 'JSON' : 'YAML';
  openDialog(`Edit ${format}`, isLocal ? 'Review the physical file diff before saving.' : 'Save a draft in this browser and download the sample YAML.',
    `<div class="edit-origin"><strong>${esc(filename)}</strong><code>${esc(edit.origin.originSelector)}</code></div>
    <form data-operation-form="update" data-entry-edit="true" novalidate>
      <div class="field-grid">${renderer.renderEntry(operation('update'), edit.kind, edit.values)}</div>
      <div class="form-errors" role="alert" hidden></div>
      <div class="form-actions"><button class="btn primary" type="submit">${icon('arrow-right')}Review changes</button>
        ${button('Cancel', 'close-dialog', undefined, 'ghost')}</div>
    </form>`, 'entry-edit', true);
  edit.initial ??= renderer.read(document.querySelector<HTMLFormElement>('[data-entry-edit]')!).args;
}

async function reviewEntry(form: HTMLFormElement, edit: EntryEdit) {
  const { args: values, errors } = renderer.read(form);
  if (errors.length) { showErrors(form, errors); return; }
  const fields = values.fields as RecordValue, extensions = (values.extensions ?? {}) as RecordValue;
  const initialFields = edit.initial!.fields as RecordValue, initialExtensions = (edit.initial!.extensions ?? {}) as RecordValue;
  const changed = (before: RecordValue, after: RecordValue) =>
    Object.fromEntries(Object.entries(after).filter(([key, value]) => json(value) !== json(before[key])));
  const unset = Object.keys({ ...initialFields, ...initialExtensions }).filter(key =>
    !Object.hasOwn(fields, key) && !Object.hasOwn(extensions, key));
  const args = { descriptor: edit.descriptor, selector: edit.entry.selector, kind: edit.kind,
    fields: changed(initialFields, fields), extensions: changed(initialExtensions, extensions), unset, dry_run: true };
  edit.values = { ...fields, ...extensions };
  const issues = renderer.validate('update', args);
  if (issues.length) { showErrors(form, issues); return; }
  showErrors(form, []);
  const submit = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  submit.disabled = true;
  try {
    const result: Result = isLocal ? await runLocal('update', args)
      : previewDraft(draftYaml[edit.origin.originDescriptor], edit.origin, args);
    if (entryEdit !== edit || !form.isConnected) return;
    if (!isLocal) {
      const modelErrors = renderer.validateEntry(edit.kind, result.after as RecordValue);
      if (modelErrors.length) { showErrors(form, modelErrors); return; }
    }
    edit.reviewed = { args, result };
    openDialog('Review changes', isLocal ? 'Confirm the diff for the authored descriptor.' : 'This preview applies to the sample draft in this browser.',
      `<div class="edit-origin"><strong>${esc(result.origin)}</strong><code>${esc(result.originSelector)}</code></div>
      ${renderPatch(String(result.patch ?? ''))}<div id="entry-review-error" class="form-errors" role="alert" hidden></div>
      <div class="form-actions">
        ${result.patch ? button(isLocal ? 'Save descriptor' : 'Save draft', 'save-entry-edit', 'check', 'primary') : ''}
        ${button('Back to edit', 'back-entry-edit', 'arrow-left', 'ghost')}
        ${result.command ? button('Copy CLI command', 'copy-entry-command', 'copy', 'ghost') : ''}
      </div>`, 'entry-review', true);
  } catch (error) {
    if (form.isConnected) showErrors(form, [(error as Error).message]);
  } finally {
    submit.disabled = false;
  }
}

async function saveEntryEdit(trigger: HTMLButtonElement) {
  const edit = entryEdit, reviewed = edit?.reviewed;
  if (!edit || !reviewed?.result.patch) return;
  trigger.disabled = true;
  try {
    const origin = String(reviewed.result.origin);
    if (isLocal) await runLocal('update', { ...reviewed.args, revision: reviewed.result.revision, dry_run: false });
    else {
      const next = { ...draftYaml, [origin]: String(reviewed.result.content) };
      const projected = projectDraft(fixture.catalog, next);
      setStored(DRAFT_YAML, next);
      draftYaml = next;
      catalog = projected;
    }
    if (entryEdit === edit) closeDialog();
    catalogPreview = null;
    if (isLocal) await reloadCatalog(edit.descriptor);
    else if (page === 'catalog') render();
    tell(isLocal ? `Saved ${origin}.` : `Draft saved. Download ${origin.split('/').at(-1)} from More actions.`);
  } catch (error) {
    const feedback = document.querySelector<HTMLElement>('#entry-review-error');
    if (entryEdit === edit && feedback) {
      feedback.hidden = false;
      feedback.textContent = (error as Error).message;
    }
  } finally {
    trigger.disabled = false;
  }
}

function setResult(item: Operation, response: Result, state?: ViewState) {
  if (item.id === 'catalog.open') {
    const opened = response as unknown as Catalog;
    if (catalog?.descriptor !== opened.descriptor) {
      catalogPreview = null;
      for (const [key, state] of viewState) if (key !== 'catalog' && key !== 'execution') {
        state.sequence += 1;
        viewState.delete(key);
      }
    }
    catalog = hasYamlDraft() ? projectDraft(opened, draftYaml) : opened;
    if (!allEntries(catalog).some(entry => entry.selector === selectedCatalog)) selectedCatalog = '$';
    if (!catalog.entries.some(row => row.selector === graphFocus)) graphFocus = '$';
    if (page === 'catalog') render();
    if (dialogTag === 'operation') closeDialog();
    tell(`Opened ${catalog.descriptor}.`);
  } else if (item.view === 'catalog') {
    if (item.id === 'activate' && isLocal) void reloadCatalog(String(response.descriptor));
    openDialog(String(response.title ?? item.label), '', response.content ? codeBlock(response.content)
      + button('Download', 'catalog-download', 'arrow-down-to-line')
      + (response.command ? button('Copy CLI command', 'copy-catalog-command', 'copy') : '') : codeBlock(response), 'catalog-result', true);
    initialized('catalog').result = response;
  } else if (state) {
    state.result = response;
    state.errors = [];
    if (page === item.view) render();
  }
}

let catalogSequence = 0;
async function reloadCatalog(descriptor?: string) {
  if (catalogBusy) return;
  catalogBusy = true;
  const sequence = ++catalogSequence;
  try {
    const result = isLocal ? await runLocal('catalog.open', descriptor ? { descriptor } : {})
      : { ...fixture.catalog };
    if (sequence === catalogSequence) setResult(operation('catalog.open'), result);
  } catch (error) { tell(error instanceof Error ? error.message : 'Unable to open catalog.'); }
  finally { catalogBusy = false; }
}

function runSample(item: Operation, args: RecordValue) {
  if (catalog && hasYamlDraft() && (['transfer.preview', 'discovery.preview', 'resolve'].includes(item.id) || item.id === 'auth.describe' && args.operation)
    && !sampleRoutesMatch(fixture.catalog, catalog)) {
    throw new Error('The draft changes the catalog used by this preview. Download the YAML and open it in the local workbench to plan the new routes.');
  }
  return runFixture(item.id === 'list' && catalog ? { ...fixture, catalog } : fixture, item, args);
}

async function submitOperation(item: Operation, args: RecordValue, state?: ViewState, form?: HTMLFormElement) {
  const errors = renderer.validate(item.id, args);
  if (errors.length) {
    if (state) state.errors = errors;
    if (form) showErrors(form, errors);
    else if (page === item.view) render();
    return;
  }
  if (item.id === 'transfer.run' && isLocal) {
    const reviewed = viewState.get('transfers-preview')?.result;
    if (!reviewed || reviewed.preview_id !== args.preview_id) {
      const messages = ['Open a transfer preview and choose Review execution for this plan.'];
      if (state) state.errors = messages;
      if (form) showErrors(form, messages);
      else if (page === item.view) render();
      return;
    }
  }
  if (state) { state.args = args; state.errors = []; }
  const sequence = state ? ++state.sequence : 0;
  const rememberCatalog = catalog?.descriptor;
  let expectedDialog = dialogEpoch;
  let confirmed = false;
  const current = () => !state ? (!form || (dialogEpoch === expectedDialog && (confirmed || form.isConnected && document.querySelector<HTMLDialogElement>('#dialog')?.open))) :
    (viewState.get(item.view) === state && state.sequence === sequence && catalog?.descriptor === rememberCatalog);
  const approved = requiresApproval(item, args);
  const submit = async () => {
    const trigger = form?.querySelector<HTMLButtonElement>('button[type="submit"]');
    if (trigger) {
      trigger.disabled = true;
      form?.setAttribute('aria-busy', 'true');
    }
    try {
      if (item.id === 'approval.request') {
        const target = operation(String(args.operation));
        const request = args.arguments;
        if (!target || !isRecord(request) || target.id === 'approval.request' || !requiresApproval(target, request)) throw new Error('Choose a credentialed operation and its exact arguments.');
        choose(target.id, request);
        if (isLocal) await submitOperation(target, request, viewState.get(target.view));
        else tell('Open a local session to approve the request.');
        return;
      }
      const result = isLocal ? await runLocal(item.id, args, approved) : runSample(item, args);
      if (current()) setResult(item, result, state);
      if (result.kind === 'job') {
        await updateJobs();
        if (page === 'execution') render();
        if (isLocal && result.job_id) monitor(String(result.job_id), item, state);
      }
    } catch (error) {
      const messages = (error instanceof Error ? error.message : 'The operation failed.').split('\n');
      if (current() && state) { state.errors = messages; if (page === item.view) render(); }
      else if (form?.isConnected) showErrors(form, messages);
      else tell(messages[0]);
    } finally {
      if (trigger) {
        trigger.disabled = false;
        form?.removeAttribute('aria-busy');
      }
    }
  };
  if (!isLocal || item.id === 'approval.request' || (!approved && !writes(item, args))) {
    await submit();
    return;
  }
  const previousResult = state?.result;
  const reviewed = item.id === 'transfer.run' ? viewState.get('transfers-preview')?.result : null;
  openDialog(approved ? 'Approve local operation' : 'Confirm local changes',
    approved ? 'Approval applies once to the operation and arguments shown below.' : 'Review the working root and fields before writing.',
    `<span class="eyeline">${esc(item.id)}</span>${codeBlock(args)}
      ${reviewed?.rows ? `<p class="muted">Reviewed plan · ${esc(reviewed.workingRoot)}</p>${codeBlock(reviewed.rows.map(row => ({ from: row.from, to: row.to, profile: row.profile })))}`
      : previousResult?.origin && writes(item, args) ? `<p class="muted">Physical descriptor · ${esc(previousResult.origin)}</p>` : ''}<div class="form-actions">
      ${button(approved ? 'Approve and run locally' : 'Confirm and apply', 'confirm-action', approved ? 'shield-check' : 'check', 'primary')}
      ${button('Cancel', 'close-dialog', undefined, 'ghost')}</div>`, 'confirm', true);
  pending = () => { expectedDialog = dialogEpoch; confirmed = true; return submit(); };
}

async function updateJobs() {
  try { jobs = isLocal ? (await get<{ jobs: Result[] }>('/api/jobs', true)).jobs : drafts(); }
  catch { jobs = []; }
}

function monitor(id: string, item: Operation, state?: ViewState) {
  window.setTimeout(async () => {
    try {
      const job = await runLocal('jobs.get', { job_id: id });
      if (state?.result?.job_id === id) state.result = job;
      await updateJobs();
      if (page === 'execution' || (page === item.view && state?.result?.job_id === id)) render();
      if (page === 'catalog' && catalogTab === 'Activity') selectCatalog(selectedCatalog, false);
      if (job.status === 'queued' || job.status === 'running') monitor(id, item, state);
    } catch { tell('Could not refresh the local job status.'); }
  }, 1200);
}

function onChange(event: Event) {
  const input = event.target as HTMLInputElement | HTMLSelectElement;
  if (input.id === 'catalog-kind') { catalogFilter = input.value as Filter; renderCatalogBrowser(); }
  if (input.id === 'snapshot-file' && input instanceof HTMLInputElement && input.files?.[0]) {
    void input.files[0].text().then(text => {
      const parsed = JSON.parse(text) as unknown;
      if (!validSnapshot(parsed)) throw new Error('The JSON is not a valid workflow snapshot.');
      draft = parsed;
      renderEditor();
      tell('Snapshot imported. Save changes to keep it.');
    }).catch(error => tell(error instanceof Error ? error.message : 'Could not import snapshot.'));
  }
  if (input.dataset.targetState) {
    const extra = input.closest('.schema-field')?.querySelector<HTMLTextAreaElement>('textarea');
    if (extra) extra.hidden = input.value !== 'custom';
  }
  if (input.dataset.dynamic) {
    const form = input.closest<HTMLFormElement>('form[data-operation-form]');
    if (!form) return;
    const item = operation(form.dataset.operationForm!);
    const { args } = renderer.read(form);
    if (input.dataset.field === 'method') {
      for (const key of Object.keys(args)) if (key !== 'method') delete args[key];
    } else if (input.dataset.field === 'kind') {
      delete args.fields;
      delete args.entry;
    } else if (input.dataset.field?.startsWith('settings.')) {
      const settings = args.settings as RecordValue;
      args.settings = input.dataset.field.endsWith('.provider') ? { provider: settings.provider } : { provider: settings.provider, mode: settings.mode };
    }
    form.querySelector('.field-grid')!.innerHTML = renderer.render(item, args);
    showErrors(form, []);
    refreshIcons(form);
    form.querySelector<HTMLElement>(`[data-field="${input.dataset.field}"]`)?.focus();
  }
}

function onInput(event: Event) {
  const input = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
  if (input.id === 'workspace-search') searchResults(input.value);
  if (input.id === 'catalog-search') { catalogSearch = input.value; renderCatalogBrowser(); }
  if (input.dataset.editPath) setEditField(input);
}

async function onSubmit(event: SubmitEvent) {
  const form = event.target as HTMLFormElement;
  if (form.id === 'snapshot-form') {
    event.preventDefault();
    if (!draft || !validSnapshot(draft)) { showErrors(form, ['Enter a valid workflow snapshot.']); return; }
    if (draft.schemaReview.some((row, i, rows) => rows.findIndex(item => item.id === row.id) !== i)) {
      showErrors(form, ['Use one schema coverage record per operation.']); return;
    }
    try {
      setStored(SNAPSHOT, draft);
      snapshot = draft;
      closeDialog();
      render();
      tell('Workflow changes saved.');
    } catch (error) { showErrors(form, [String(error)]); }
    return;
  }
  const id = form.dataset.operationForm;
  if (!id) return;
  event.preventDefault();
  if (form.dataset.entryEdit && entryEdit) { await reviewEntry(form, entryEdit); return; }
  const item = operation(id);
  const { args, errors } = renderer.read(form);
  if (errors.length) { showErrors(form, errors); return; }
  const state = item.view === 'catalog' ? undefined : initialized(item.view);
  await submitOperation(item, args, state, form);
}

function drawCatalogPreview(scroll = false) {
  const panel = document.querySelector<HTMLElement>('#catalog-preview');
  if (!panel) return;
  if (!catalogPreview || catalogPreview.descriptor !== catalog?.descriptor || catalogPreview.selector !== selectedCatalog) {
    panel.innerHTML = '';
    return;
  }
  const item = operation(catalogPreview.operation);
  const more = catalogPreview.operation === 'discovery.preview' && isLocal && catalogPreview.result.kind === 'discovery'
    ? button('Review discovery run', 'review-catalog-discovery', 'shield-check')
    : button(`Open in ${sections[item.view].title}`, 'open-preview-view', 'arrow-right');
  panel.innerHTML = `<section class="card catalog-preview-card" aria-live="polite">${renderResult(catalogPreview.result, sections[item.view].glyph, isLocal)}
    <div class="card-foot actions">${more}${button('Close preview', 'close-catalog-preview', 'x', 'ghost')}</div></section>`;
  refreshIcons(panel);
  if (scroll) panel.scrollIntoView({ block: 'nearest' });
}

async function previewFromCatalog(id: string, args: RecordValue) {
  const descriptor = catalog?.descriptor;
  const selector = selectedCatalog;
  if (!descriptor) return;
  const item = operation(id);
  const result: Result = { title: 'Building preview', content: 'Reading the selected scope…' };
  const pendingPreview = { descriptor, selector, operation: id, result, args };
  catalogPreview = pendingPreview;
  drawCatalogPreview();
  try {
    const response = isLocal ? await runLocal(item.id, args) : runSample(item, args);
    if (catalogPreview !== pendingPreview || descriptor !== catalog?.descriptor || selector !== selectedCatalog) return;
    pendingPreview.result = response;
  } catch (error) {
    if (catalogPreview !== pendingPreview) return;
    pendingPreview.result = { title: 'Preview unavailable', content: error instanceof Error ? error.message : 'Could not plan this scope.' };
  }
  drawCatalogPreview(true);
}

function catalogAction(action: string) {
  const row = catalog && allEntries(catalog).find(item => item.selector === selectedCatalog);
  const descriptor = catalog ? { descriptor: catalog.descriptor } : {};
  const args = row ? { ...descriptor, selector: row.selector } : descriptor;
  const actions: Record<string, () => void> = {
    'open-catalog': () => choose('catalog.open', descriptor),
    'set-active': () => choose('activate', descriptor),
    'refresh-catalog': () => void reloadCatalog(catalog?.descriptor),
    'catalog-list': () => choose('list', { ...args, kind: 'all', format: 'json' }),
    'copy-selector': () => row && void copy(row.selector),
    'edit-entry': () => row && openEntryEditor(row),
    'edit-link': () => row && openEntryEditor(row, true),
    'edit-declaration': () => {
      const owner = catalog?.entries.find(entry => entry.selector === row?.targetOrigin?.selector);
      if (owner) openEntryEditor(owner);
    },
    'resolve-entry': () => void previewFromCatalog('resolve', { ...args, online: false, write: false }),
    'discover-entry': () => void previewFromCatalog('discovery.preview', args),
    'pull-entry': () => void previewFromCatalog('transfer.preview', { ...args, direction: 'pull' }),
    'push-entry': () => void previewFromCatalog('transfer.preview', { ...args, direction: 'push' }),
    'catalog-diagram': () => choose('diagram', { ...descriptor, format: 'svg', detail: 'summary' }),
    'download-yaml': () => {
      if (isLocal || !row || !Object.hasOwn(draftYaml, row.originDescriptor)) return;
      download(row.originDescriptor.split('/').at(-1)!, draftYaml[row.originDescriptor], 'application/yaml');
    },
    'reset-yaml': () => {
      openDialog('Restore sample catalog', 'This clears the saved sample YAML drafts in this browser.',
        `<div class="form-actions">${button('Restore sample', 'confirm-action', 'refresh-ccw', 'primary')}${button('Cancel', 'close-dialog', undefined, 'ghost')}</div>`,
        'confirm-reset-yaml');
      pending = () => {
        localStorage.removeItem(DRAFT_YAML);
        draftYaml = { ...fixture.yaml };
        catalog = fixture.catalog;
        catalogPreview = null;
        if (!allEntries(catalog).some(entry => entry.selector === selectedCatalog)) selectedCatalog = graphFocus;
        render();
        tell('Published sample restored.');
      };
    },
    'add-entry': () => choose('add', { ...descriptor, kind: 'resource', parent: row?.kind === 'catalog' && row.selector !== '$' ? row.selector : undefined, dry_run: true }),
  };
  const run = actions[action];
  if (run) run();
  return !!run;
}

function sample(action: string) {
  const descriptor = fixture?.catalog.descriptor;
  const examples: Record<string, [string, RecordValue, boolean]> = {
    'sample-edit': ['update', { descriptor, selector: union, kind: 'resource', dry_run: true }, false],
    'sample-resolve': ['resolve', { descriptor, selector: mapping, online: false, write: false }, true],
    'sample-discovery': ['discovery.preview', { descriptor, selector: releases }, true],
    'sample-profiles': ['auth.profile.list', { config: 'profiles.yaml' }, true],
    'sample-profile': ['auth.profile.save', defaultArgs('auth.profile.save'), false],
    'sample-pull': ['transfer.preview', { descriptor, selector: mapping, direction: 'pull' }, true],
    'sample-push': ['transfer.preview', { descriptor, selector: union, direction: 'push' }, true],
    'sample-diagram': ['diagram', { descriptor, format: 'svg', detail: 'summary' }, true],
    'sample-migrate': ['migrate', defaultArgs('migrate'), true],
    'sample-clone': ['clone.descriptor', defaultArgs('clone.descriptor'), true],
    'sample-completion': ['cli.completion.show', { shell: 'bash' }, true],
  };
  const selection = examples[action];
  if (selection) choose(...selection);
}

function onClick(event: MouseEvent) {
  const target = (event.target as Element).closest<HTMLElement>('button, a');
  if (!target) return;
  const data = target.dataset;
  if (data.authLocation && catalog) {
    const location = allEntries(catalog).find(entry => entry.selector === data.authLocation);
    const consumer = selectedCatalog !== data.authLocation ? selectedCatalog
      : catalog.relationships.some(edge => edge.kind === 'target' && edge.from === graphFocus && edge.to === data.authLocation)
        ? graphFocus : location?.parent;
    if (consumer) void previewFromCatalog('auth.describe', { descriptor: catalog.descriptor, selector: consumer,
      operation: data.authOperation, location: data.authLocation });
    return;
  }
  if (data.authNext === 'configure') {
    return choose('auth.profile.save', { settings: { provider: data.provider, mode: data.mode } });
  }
  if (isLocal && data.authNext === 'check' && data.profile) {
    return choose('auth.check', { profile: data.profile, ...(data.url ? { url: data.url } : {}) });
  }
  if (isLocal && data.authNext === 'login' && data.profile) {
    return choose('auth.login.' + data.provider, { method: 'saved', profile: data.profile });
  }
  if (data.operation) return choose(data.operation);
  if (data.schema) { schemaSelection = data.schema; render(); return; }
  if (data.select) {
    selectCatalog(data.select, true);
    if (target.classList.contains('graph-node') || target.classList.contains('graph-parent')) {
      document.querySelector<HTMLButtonElement>(`.graph-node[data-select="${CSS.escape(data.select)}"]`)?.focus({ preventScroll: true });
    }
    return;
  }
  if (data.catalogMode === 'list' || data.catalogMode === 'graph') {
    catalogMode = data.catalogMode;
    render();
    document.querySelector<HTMLButtonElement>(`[data-catalog-mode="${catalogMode}"]`)?.focus({ preventScroll: true });
    return;
  }
  if (data.catalogTab) { catalogTab = data.catalogTab; selectCatalog(selectedCatalog, false); return; }
  if (data.searchPage) { closeDialog(); return navigate(data.searchPage); }
  if (data.searchOperation) {
    const op = operation(data.searchOperation);
    if (op.view !== 'catalog') closeDialog();
    return choose(op.id);
  }
  if (data.searchCatalog) { selectedCatalog = data.searchCatalog; closeDialog(); return navigate('catalog'); }
  if (data.job) return choose('jobs.get', { job_id: data.job }, true);
  if (data.removeRow && draft && editSection) {
    draft[editSection].splice(Number(data.removeRow), 1);
    renderEditor();
    return;
  }
  const action = data.action;
  if (!action) return;
  if (catalogAction(action)) return;
  if (action.startsWith('sample-')) return sample(action);
  const viewResult = page === 'catalog' ? catalogPreview?.result : viewState.get(page)?.result;
  const result = renderResultData(viewResult);
  switch (action) {
    case 'toggle-menu': menu(!mobileOpen); break;
    case 'close-menu': menu(false); break;
    case 'close-dialog': closeDialog(); break;
    case 'search': searchDialog(); break;
    case 'workspace-help':
      openDialog('Fileroute workbench', 'Use the catalog for resource actions and the engineering views for parity, evidence, and acceptance.',
        `<div class="actions"><a href="#architecture" class="btn primary" data-action="close-dialog">Local setup ${icon('arrow-right')}</a>
        <a href="#verification" class="btn secondary" data-action="close-dialog">Verification ${icon('arrow-right')}</a></div>`, 'help');
      break;
    case 'edit-workflow': startEditor(); break;
    case 'edit-parity':
    case 'edit-tasks':
    case 'edit-evidence':
    case 'edit-decisions':
    case 'edit-gates':
    case 'edit-schemaReview': startEditor(action.slice(5) as Section); break;
    case 'export-workflow': download('fileroute-workflow.json', json(draft ?? snapshot)); break;
    case 'import-workflow': document.querySelector<HTMLInputElement>('#snapshot-file')?.click(); break;
    case 'reset-workflow': draft = structuredClone(original); renderEditor(); tell('Published snapshot loaded. Save changes to keep it.'); break;
    case 'add-row':
      if (draft && editSection) {
        const row = Object.fromEntries([['id', crypto.randomUUID()], ...fields[editSection].map(key => [key,
          key === 'id' ? operations.find(op => !draft!.schemaReview.some(item => item.id === op.id))?.id ?? operations[0].id : ''])]);
        (draft[editSection] as RecordValue[]).push(row);
        renderEditor();
        document.querySelector<HTMLDetailsElement>('.edit-item:last-of-type')?.setAttribute('open', '');
      }
      break;
    case 'copy-next': void copy(snapshot.next.commands.join('\n')); break;
    case 'copy-schema': void copy(json(renderer.schemaFor(schemaSelection))); break;
    case 'copy-command': if (result) void copy(String(result.command)); break;
    case 'copy-entry-command': if (entryEdit?.reviewed?.result.command) void copy(String(entryEdit.reviewed.result.command)); break;
    case 'copy-catalog-command': if (initialized('catalog').result?.command) void copy(String(initialized('catalog').result?.command)); break;
    case 'back-entry-edit': if (entryEdit) showEntryEditor(entryEdit); break;
    case 'save-entry-edit': void saveEntryEdit(target as HTMLButtonElement); break;
    case 'copy-result': if (result?.content) void copy(result.content); break;
    case 'copy-job':
      if (viewResult) void copy(json({ operation: viewResult.operation, arguments: viewResult.scope }));
      break;
    case 'copy-request': {
      const form = target.closest<HTMLFormElement>('form');
      if (!form) break;
      const { args, errors } = renderer.read(form);
      if (errors.length) showErrors(form, errors);
      else void copy(json({ operation: form.dataset.operationForm, arguments: args }));
      break;
    }
    case 'download-result':
      if (result?.content) download(String(result.filename ?? (result.format === 'json' ? 'output.json' : 'output.txt')),
        result.content, result.format === 'svg' ? 'image/svg+xml' : 'text/plain');
      break;
    case 'catalog-download': {
      const payload = initialized('catalog').result;
      if (payload?.content) download(payload.format === 'json' ? 'catalog-inspection.json' : 'catalog-inspection.txt', payload.content, 'text/plain');
      break;
    }
    case 'download-companion':
      if (result?.companion) download(String(result.filename ?? 'diagram').replace(/\.[^.]+$/, '.svg'), String(result.companion), 'image/svg+xml');
      break;
    case 'close-catalog-preview': catalogPreview = null; drawCatalogPreview(); break;
    case 'open-preview-view':
      if (catalogPreview) {
        const item = operation(catalogPreview.operation);
        const state = makeState(item.id, catalogPreview.args);
        state.result = catalogPreview.result;
        viewState.set(item.view, state);
        navigate(item.view);
      }
      break;
    case 'review-catalog-discovery':
      if (catalogPreview?.operation === 'discovery.preview') choose('discovery.run', { ...catalogPreview.args, format: 'text' });
      break;
    case 'open-job-entry': {
      const context = viewResult?.catalogScope as { descriptor: string; selector: string } | undefined;
      if (!context) break;
      void (async () => {
        if (context.descriptor !== catalog?.descriptor) await reloadCatalog(context.descriptor);
        if (context.descriptor === catalog?.descriptor) {
          catalogTab = 'Activity';
          navigate('catalog?view=graph&select=' + encodeURIComponent(context.selector));
        }
      })();
      break;
    }
    case 'review-transfer':
      if (result?.preview_id) {
        viewState.set('transfers-preview', { ...makeState('transfer.preview', {}), result });
        choose('transfer.run', { preview_id: result.preview_id });
      }
      break;
    case 'review-discovery-save':
      if (result?.result_id) choose('discovery.save', { result_id: result.result_id, output: result.suggestedOutput ?? 'discovered.yaml' });
      break;
    case 'confirm-action': {
      const run = pending;
      closeDialog();
      if (run) void run();
      break;
    }
    case 'refresh-jobs': void updateJobs().then(() => { if (page === 'execution') render(); }); break;
    case 'clear-drafts':
      try { localStorage.removeItem('fileroute-drafts-v1'); jobs = []; render(); }
      catch { tell('Could not clear browser drafts.'); }
      break;
  }
}

void startup().catch(error => {
  app.innerHTML = `<main class="boot"><img src="/fileroute.svg" alt="" /><h1>Fileroute could not load</h1><p>${esc(error instanceof Error ? error.message : error)}</p>
    <button class="btn primary" type="button" id="retry">Reload</button></main>`;
  document.querySelector('#retry')?.addEventListener('click', () => location.reload());
});
