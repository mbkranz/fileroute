import { allEntries } from './catalog';
import { esc, icon } from './components';
import type { Catalog, CatalogEntry, Relationship } from './types';

function node(entry: CatalogEntry, selected: string, detail: string, edge?: Relationship) {
  const glyph = entry.kind === 'catalog' ? 'folder' : entry.provider ? 'globe' : 'file-text';
  const name = entry.kind === 'location' ? entry.provider ?? (edge?.kind === 'source' ? 'Local source' : 'Destination')
    : entry.entity.title ?? entry.name ?? 'Catalog';
  return `<button class="graph-node" type="button" data-select="${esc(entry.selector)}" aria-pressed="${entry.selector === selected}">
    ${icon(glyph)}<span><strong>${esc(name)}</strong>${edge?.inherited ? '<em class="graph-inherited">Inherited</em>' : ''}<small>${esc(detail)}</small></span></button>`;
}

export function graph(catalog: Catalog, focus: string, selected: string) {
  const entries = new Map(allEntries(catalog).map(entry => [entry.selector, entry]));
  const current = entries.get(focus) ?? catalog.entries[0];
  if (!current) return '<p class="empty">Select an entry to show its relationships.</p>';
  const sources: string[] = [], targets: string[] = [], children: string[] = [];
  let parent: CatalogEntry | undefined;
  for (const edge of catalog.relationships) {
    const from = entries.get(edge.from), to = entries.get(edge.to);
    if (!from || !to) continue;
    if (edge.kind === 'source' && edge.to === current.selector) sources.push(node(from, selected, from.artifactPath ?? '', edge));
    if (edge.kind === 'target' && edge.from === current.selector) targets.push(node(to, selected, to.artifactPath ?? '', edge));
    if (edge.kind === 'contains' && edge.from === current.selector) children.push(node(to, selected, to.kind));
    if (edge.kind === 'contains' && edge.to === current.selector) parent = from;
  }
  return `<div class="graph-explorer">
    ${parent ? `<button class="graph-parent" type="button" data-select="${esc(parent.selector)}">${icon('arrow-up')}${esc(parent.entity.title ?? parent.name ?? catalog.descriptor)}</button>` : ''}
    <div class="graph-flow" aria-label="Sources, selected entry, and publish destinations">
      <svg class="graph-edges" aria-hidden="true">
        <defs><marker id="catalog-edge-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M1 1L7 4L1 7" fill="none" stroke="currentColor"/></marker></defs><g></g>
      </svg>
      <div class="graph-column graph-sources"><h3>Sources</h3>
        ${sources.join('') || '<p class="graph-empty">None declared</p>'}</div>
      <div class="graph-column graph-center"><h3>${current.kind === 'catalog' ? 'Catalog' : 'Local artifact'}</h3>
        ${node(current, selected, current.artifactPath ?? current.qualifiedName ?? catalog.descriptor)}</div>
      <div class="graph-column graph-targets"><h3>Publish to</h3>
        ${targets.join('') || '<p class="graph-empty">None declared</p>'}</div>
    </div>
    ${children.length ? `<section class="graph-children"><h3>Contains</h3><div>${children.slice(0, 6).join('')}</div>
      ${children.length > 6 ? `<details><summary>Show ${children.length - 6} more</summary><div>${children.slice(6).join('')}</div></details>` : ''}
    </section>` : ''}
  </div>`;
}

export function observeEdges(flow: HTMLElement) {
  const svg = flow.querySelector('svg')!;
  const paths = svg.querySelector('g')!;
  const center = flow.querySelector('.graph-center > button')!;
  const upstream = [...flow.querySelectorAll('.graph-sources > button')];
  const downstream = [...flow.querySelectorAll('.graph-targets > button')];
  const routes = [...upstream.map(source => [source, center]), ...downstream.map(target => [center, target])];
  const draw = () => {
    const bounds = flow.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const vertical = getComputedStyle(flow).getPropertyValue('--graph-vertical').trim() === '1';
    svg.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    paths.replaceChildren(...routes.map(([source, target]) => {
      const a = source.getBoundingClientRect(), b = target.getBoundingClientRect();
      const x1 = (vertical ? a.left + a.width / 2 : a.right) - bounds.left;
      const y1 = (vertical ? a.bottom : a.top + a.height / 2) - bounds.top;
      const x2 = (vertical ? b.left + b.width / 2 : b.left - 3) - bounds.left;
      const y2 = (vertical ? b.top - 3 : b.top + b.height / 2) - bounds.top;
      const midX = (x1 + x2) / 2, midY = (y1 + y2) / 2;
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', vertical ? `M${x1} ${y1} C${x1} ${midY} ${x2} ${midY} ${x2} ${y2}`
        : `M${x1} ${y1} C${midX} ${y1} ${midX} ${y2} ${x2} ${y2}`);
      path.setAttribute('marker-end', 'url(#catalog-edge-arrow)');
      return path;
    }));
  };
  const observer = new ResizeObserver(draw);
  observer.observe(flow);
  draw();
  return observer;
}
