import '@awesome.me/webawesome/dist/components/tree/tree.js';
import '@awesome.me/webawesome/dist/components/tree-item/tree-item.js';
import '@awesome.me/webawesome/dist/components/split-panel/split-panel.js';
import { badge, button, codeBlock, esc, icon } from './components';
import { title } from './project';
import type { Catalog, CatalogEntry, LocationRecord, Result } from './types';

export type Filter = 'all' | 'catalog' | 'resource' | 'location';
export type CatalogMode = 'list' | 'graph';

export function allEntries(catalog: Catalog) {
  return catalog.entries.flatMap(row => [row, ...(row.locations ?? [])]);
}

export function catalogView(catalog: Catalog | null, selected: string, tab: string, mode: CatalogMode, local: boolean, jobs: Result[], drafted: boolean) {
  return `${title('Catalog explorer', 'Find an entry, trace its routes, or work with its YAML.', `<div class="actions">${button('Open catalog', 'open-catalog', 'folder')}${button('Set active', 'set-active', 'check')}</div>`)}
    ${catalog ? `
      <div class="catalog-context"><div>${icon('files')}<span>Open descriptor</span><code>${esc(catalog.descriptor)}</code></div>
        <div>${icon('check')}<span>CLI active</span><code>${esc(catalog.activeDescriptor ?? 'Default lookup')}</code></div>
        ${!local ? badge(drafted ? 'Sample draft in this browser' : 'Sample catalog', 'quiet') : ''}
        ${drafted ? button('Restore sample', 'reset-yaml', 'refresh-ccw', 'ghost') : button('Refresh', 'refresh-catalog', 'refresh-ccw', 'ghost')}
      </div>
      <wa-split-panel class="catalog-layout ${mode === 'graph' ? 'graph-layout' : ''}" position="${mode === 'graph' ? 51 : 33}" ${matchMedia("(max-width: 590px)").matches ? "orientation=vertical disabled" : ""}>
        <section class="card catalog-tree" slot="start">
          <div class="section-heading catalog-view-heading"><h2>Catalog</h2><div class="catalog-mode" role="group" aria-label="Catalog view">
             <button type="button" data-catalog-mode="list" aria-pressed="${mode === 'list'}">${icon('list-checks')}List</button>
             <button type="button" data-catalog-mode="graph" aria-pressed="${mode === 'graph'}">${icon('network')}Graph</button>
          </div></div>
          <div class="catalog-filters" ${mode === 'graph' ? 'hidden' : ''}><label class="search-label" for="catalog-search">${icon('search')}<input type="search" id="catalog-search" placeholder="Find a resource…" /></label>
            <label class="filter-label"><span class="sr-only">Entry type</span><select id="catalog-kind"><option value="all">All types</option><option value="catalog">Catalogs</option><option value="resource">Resources</option><option value="location">Locations</option></select></label></div>
          <div id="catalog-tree-items"></div>
        </section>
        <section id="catalog-inspector" class="card catalog-inspector" slot="end"><div id="catalog-detail">${inspect(catalog, selected, tab, local, jobs, drafted)}</div><div id="catalog-preview"></div></section>
      </wa-split-panel>` : `<section class="card empty-card">${icon('folder-tree')}<h2>Open a descriptor</h2><p>Choose a YAML or JSON catalog from the current working root.</p>${button('Open catalog', 'open-catalog', 'folder', 'primary')}</section>`}`;
}

export function tree(catalog: Catalog, search: string, filter: Filter, selected: string) {
  const query = search.trim().toLowerCase();
  const records = allEntries(catalog);
  const direct = records.filter(item => {
    const name = [item.entity.title, item.name, item.qualifiedName, item.artifactPath, item.selector, item.provider].filter(Boolean).join(' ').toLowerCase();
    return (filter === 'all' || item.kind === filter) && (!query || name.includes(query));
  });
  if (!direct.length) return '<p class="empty">No catalog entries match these filters.</p>';
  const shown = records.filter(item => direct.some(hit => hit.selector === item.selector || hit.pointer.startsWith(item.pointer + '/')));
  const selectedPath = records.find(item => item.selector === selected)?.pointer;
  const descendants = (item: CatalogEntry): string => {
    const children = shown.filter(child => child.parent === item.selector && child.selector !== item.selector);
    const label = item.entity.title ?? item.name ?? catalog.descriptor;
    const glyph = item.kind === 'catalog' ? 'folder' : item.kind === 'location' ? 'route' : 'file-text';
    const expand = children.length && (query || filter !== 'all' || item.selector === '$' || selectedPath?.startsWith(item.pointer + '/'));
    return `<wa-tree-item data-selector="${esc(item.selector)}" ${expand ? 'data-expand' : ''} ${selected === item.selector ? 'selected' : ''}>
      <span slot="expand-icon">${icon('chevron-right')}</span><span slot="collapse-icon">${icon('chevron-down')}</span>
      <span class="tree-item-label">${icon(glyph)}<span>${esc(label)}</span>${item.linked ? '<small>LINK</small>' : ''}</span>
      ${children.map(descendants).join('')}</wa-tree-item>`;
  };
  return `<wa-tree selection="single" aria-label="Catalog resources"><span slot="expand-icon">${icon('chevron-right')}</span><span slot="collapse-icon">${icon('chevron-down')}</span>${shown.filter(item => item.selector === '$').map(descendants).join('')}</wa-tree>
    ${query || filter !== 'all' ? `<p class="tree-count">${direct.length} match${direct.length === 1 ? '' : 'es'}</p>` : ''}`;
}

function detailsRow(name: string, content: string) {
  return `<div class="resource-row"><dt>${esc(name)}</dt><dd>${content}</dd></div>`;
}

function location(source: LocationRecord, provider?: string, selector?: string, authOperation?: string) {
  return `<div class="location-row"><span class="location-icon">${icon(provider ? 'globe' : 'file-text')}</span><div>
    ${selector ? `<button type="button" class="path-button" data-select="${esc(selector)}"><code>${esc(source.path)}</code></button>` : `<code>${esc(source.path)}</code>`}
    ${source.resourcePathTemplate ? `<small>Template · <code>${esc(source.resourcePathTemplate)}</code></small>` : ''}
    ${source.authProfile ? `<small>Profile · ${esc(source.authProfile)}</small>` : ''}
    ${provider && selector && authOperation ? authAction(selector, authOperation) : ''}
    </div>${provider ? badge(provider, 'quiet') : ''}</div>`;
}

function authAction(selector: string, operation: string) {
  return `<button type="button" class="btn ghost" data-auth-location="${esc(selector)}" data-auth-operation="${esc(operation)}">How do I connect?</button>`;
}

export function inspect(catalog: Catalog, selector: string, tab: string, local: boolean, jobs: Result[], drafted: boolean) {
  const item = allEntries(catalog).find(row => row.selector === selector) ?? catalog.entries[0];
  if (!item) return '<p class="empty">Choose a catalog entry to inspect.</p>';
  const name = item.entity.title ?? item.name ?? catalog.descriptor;
  const sources = (item.locations ?? []).filter(row => row.pointer.startsWith(item.pointer + '/sources/'));
  const targets = item.effectiveTargets ?? [];
  const sourceLabel = sources.length === 1 && sources[0].provider ? 'Download from'
    : sources.length > 0 && sources.every(source => !source.provider && !String(source.artifactPath).includes('://')) ? 'Derived from' : 'Sources';
  const authored = Object.hasOwn(item.entity, 'targets') ? item.entity.targets === null ? 'Explicit null · inherits'
    : item.entity.targets?.length ? 'Explicit destinations' : 'Publication disabled' : 'Inherited if available';
  const scope = item.kind === 'catalog' || item.kind === 'resource';
  const originBadge = item.linked ? 'Linked catalog' : item.kind;
  const descendants = scope ? catalog.entries.filter(row => row.pointer.startsWith(item.pointer + '/')) : [];
  const subtree = [item, ...descendants];
  const discovery = subtree.some(row => row.locations?.some(location => location.pointer.startsWith(row.pointer + '/sources/')
    && location.entity.resourcePathTemplate));
  const leaves = subtree.filter(row => !subtree.some(other => other !== row && other.parent === row.selector));
  const inputs = leaves.filter(row => row.locations?.some(location => location.pointer.startsWith(row.pointer + '/sources/')));
  const downloadable = inputs.length > 0 && !discovery && inputs.every(row => {
    const remote = row.locations!.filter(location => location.pointer.startsWith(row.pointer + '/sources/'));
    return remote.length === 1 && !!remote[0].provider && !!row.artifactPath;
  });
  const path = item.originDescriptor;
  const tabNames = ['Details', 'Authored JSON', 'Provenance', 'Activity'];
  const scopedJobs = jobs.filter(job => {
    const context = job.catalogScope as { descriptor?: string; selector?: string } | undefined;
    return context?.descriptor === catalog.descriptor && context.selector === item.selector;
  });
  const available = [
    button(path.endsWith('.json') ? 'Edit JSON' : 'Edit YAML', 'edit-entry', 'pencil'),
    button('Resolve', 'resolve-entry', 'route'),
    ...(item.kind === 'location' && item.provider ? [authAction(item.selector, /\/targets\/\d+$/.test(item.pointer)
      ? 'push' : item.entity.resourcePathTemplate ? 'discover' : 'pull')] : []),
    ...(scope && discovery ? [button('Discover', 'discover-entry', 'search')] : []),
    ...(scope && downloadable ? [button('Preview download', 'pull-entry', 'arrow-down-to-line', 'primary')] : []),
    ...(scope && subtree.some(row => row.effectiveTargets.length && row.artifactPath) ? [button('Preview publish', 'push-entry', 'upload', 'primary')] : []),
  ];

  return `<header class="resource-heading"><div class="resource-type"><span class="metric-icon olive">${icon(item.kind === 'catalog' ? 'folder' : 'file-text')}</span>${badge(originBadge, 'quiet')}</div>
    <h2>${esc(name)}</h2>${item.entity.description ? `<p>${esc(item.entity.description)}</p>` : ''}
    <div class="selector-block"><code>${esc(item.selector)}</code>${button('Copy selector', 'copy-selector', 'copy', 'ghost')}</div>
    <div class="actions inspector-actions">${available.join('')}
      <details class="inspector-menu"><summary>${icon('ellipsis')}<span>More actions</span></summary><div>
        ${button('Inspect or export', 'catalog-list', 'list-checks', 'ghost')}
        ${item.kind === 'catalog' ? button('Add entry', 'add-entry', 'plus', 'ghost') : ''}
        ${item.selector === '$' ? button('Export diagram', 'catalog-diagram', 'network', 'ghost') : ''}
        ${item.link && local ? button('Edit catalog link', 'edit-link', 'route', 'ghost') : ''}
        ${!local && drafted ? button('Download YAML', 'download-yaml', 'arrow-down-to-line', 'ghost') : ''}</div></details>
    </div></header>
    <div class="tabs" aria-label="Resource details">${tabNames.map(name =>
      `<button type="button" data-catalog-tab="${esc(name)}" class="${tab === name ? 'active' : ''}" aria-pressed="${tab === name}">${esc(name)}</button>`).join('')}</div>
    ${tab === 'Activity' ? `<div class="panel-body">${scopedJobs.length ? `<ul class="node-jobs">${scopedJobs.slice().reverse().map(job =>
      `<li><button type="button" data-job="${esc(job.job_id)}">${icon('activity')}${esc(job.operation)}</button>${badge(String(job.status))}</li>`).join('')}</ul>`
      : '<p class="empty">No executions for this selection.</p>'}</div>`
    : tab === 'Authored JSON' ? `<div class="panel-body">${codeBlock(item.entity)}</div>` : tab === 'Provenance' ? `<div class="panel-body">
      <dl class="resource-details">${detailsRow('Physical descriptor', `<code>${esc(item.originDescriptor)}</code>`)}
      ${detailsRow('Origin selector', `<code>${esc(item.originSelector)}</code>`)}
      ${detailsRow('JSON Pointer', `<code>${esc(item.pointer || '/')}</code>`)}
      ${detailsRow('Target declaration', item.targetOrigin ? `<code>${esc(item.targetOrigin.descriptor)}</code><br/><code>${esc(item.targetOrigin.selector)}</code>` : 'No target declaration')}
      ${detailsRow('Authored targets', authored)}</dl>
    </div>` : `<div class="panel-body"><dl class="resource-details">
      ${detailsRow(item.kind === 'location' ? 'Location path' : 'Local file', item.artifactPath ? `<code>${esc(item.artifactPath)}</code>` : 'No local path')}
      ${detailsRow(sourceLabel, sources.length ? sources.map(source => location(source.entity as unknown as LocationRecord, source.provider,
        source.selector, source.entity.resourcePathTemplate ? 'discover' : 'pull')).join('') : 'No declared source')}
      ${detailsRow('Publish to', targets.length ? targets.map((target, index) => {
        const owner = catalog.entries.find(row => row.selector === item.targetOrigin?.selector);
        const declared = owner?.locations?.filter(row => row.pointer.startsWith(owner.pointer + '/targets/'))[index];
        return location(target, declared?.provider ?? target.serviceType, declared?.selector, 'push');
      }).join('') + `<small class="muted">Declared in ${esc(item.targetOrigin?.descriptor ?? item.originDescriptor)}</small>
        ${item.targetOrigin?.selector && item.targetOrigin.selector !== item.selector ? button('Edit declaration', 'edit-declaration', 'pencil', 'ghost') : ''}`
        : `<span>${item.entity.targets?.length === 0 ? 'Publication is disabled for this scope.' : 'No effective publish destination.'}</span>`)}
      ${detailsRow('Physical origin', `<code>${esc(item.originDescriptor)}</code>`)}
    </dl></div>`}`;
}
