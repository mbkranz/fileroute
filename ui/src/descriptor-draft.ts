import { createTwoFilesPatch } from 'diff';
import { isScalar, parseDocument } from 'yaml';
import { isRecord, json } from './components';
import type { Catalog, CatalogEntry, DescriptorOrigin, LocationRecord, RecordValue, Relationship } from './types';

export const DRAFT_YAML = 'fileroute-sample-yaml-v1';

function parse(text: string) {
  const document = parseDocument(text, { prettyErrors: false });
  if (document.errors.length) throw new Error(document.errors[0].message);
  return document;
}

function pointerParts(pointer: string) {
  return pointer ? pointer.slice(1).split('/').map(part => part.replaceAll('~1', '/').replaceAll('~0', '~')) : [];
}

function atPointer(value: unknown, pointer: string): RecordValue {
  for (const part of pointerParts(pointer)) {
    value = Array.isArray(value) ? value[Number(part)] : isRecord(value) && Object.hasOwn(value, part) ? value[part] : undefined;
  }
  if (!isRecord(value)) throw new Error('The selected YAML entry is unavailable.');
  return value;
}

export function previewDraft(text: string, origin: DescriptorOrigin, args: RecordValue) {
  const document = parse(text);
  const path = pointerParts(origin.originPointer);
  const before = atPointer(document.toJS({ maxAliasCount: 50 }), origin.originPointer);
  const changes = { ...args.fields as RecordValue, ...args.extensions as RecordValue };
  const removed = Array.isArray(args.unset) ? args.unset.map(String) : [];
  for (const [key, value] of Object.entries(changes)) {
    if (json(before[key]) === json(value)) continue;
    const field = [...path, key];
    const existing = document.getIn(field, true);
    if (isScalar(existing) && (typeof value !== 'object' || value === null)) existing.value = value;
    else document.setIn(field, value);
  }
  for (const key of removed) document.deleteIn([...path, key]);
  const after = atPointer(document.toJS({ maxAliasCount: 50 }), origin.originPointer);
  const content = json(before) === json(after) ? text : document.toString({ lineWidth: 0 });
  const patch = content === text ? '' : createTwoFilesPatch(
    'a/' + origin.originDescriptor, 'b/' + origin.originDescriptor, text, content,
  ).split('\n').slice(1).join('\n');
  return { kind: 'diff', title: 'YAML change review', origin: origin.originDescriptor, before, after,
    content, patch, originSelector: origin.originSelector, verification: 'Browser draft' };
}

function provider(location: LocationRecord) {
  return location.serviceType ?? (
    /^(s3:\/\/|https:\/\/[^/]*\.amazonaws\.com)/i.test(location.path) ? 'S3'
      : /^https:\/\/[^/]*\.sharepoint\.com/i.test(location.path) ? 'SharePoint'
        : /^https:\/\/(drive|docs)\.google\.com/i.test(location.path) ? 'GoogleDrive' : undefined
  );
}

export function projectDraft(base: Catalog, yaml: Record<string, string>): Catalog {
  const files = new Map(Object.entries(yaml).map(([name, text]) => [name, parse(text).toJS({ maxAliasCount: 50 }) as unknown]));
  const seen = new Map<string, CatalogEntry>();
  const relationships: Relationship[] = base.relationships.filter(edge => edge.kind === 'contains');
  const entries = base.entries.map(row => {
    const record = atPointer(files.get(row.originDescriptor), row.originPointer);
    const { catalogs: _catalogs, resources: _resources, ...entity } = record;
    const parent = seen.get(row.parent);
    const locations = (['sources', 'targets'] as const).flatMap(field => {
      const list = entity[field];
      return Array.isArray(list) ? (list as LocationRecord[]).map((location, index): CatalogEntry => {
        const suffix = '/' + field + '/' + index;
        return {
          kind: 'location', selector: row.selector + '.' + field + '[' + index + ']',
          parent: row.selector, pointer: row.pointer + suffix, originPointer: row.originPointer + suffix,
          originSelector: row.originSelector + '.' + field + '[' + index + ']', originDescriptor: row.originDescriptor,
          revision: row.revision, name: field + '[' + index + ']', qualifiedName: row.qualifiedName,
          artifactPath: location.path, entity: { ...location }, provider: provider(location), effectiveTargets: [],
        };
      }) : [];
    });
    const targetOrigin = Array.isArray(entity.targets)
      ? { descriptor: row.originDescriptor, selector: row.selector } : parent?.targetOrigin ?? null;
    const effectiveTargets = Array.isArray(entity.targets) ? entity.targets as LocationRecord[] : parent?.effectiveTargets ?? [];
    const entry: CatalogEntry = { ...row, entity, effectiveTargets, targetOrigin, locations,
      artifactPath: typeof entity.path === 'string' ? entity.path : null };
    seen.set(row.selector, entry);
    for (const source of locations.filter(item => item.pointer.startsWith(row.pointer + '/sources/'))) {
      relationships.push({ from: source.selector, to: row.selector, kind: 'source' });
    }
    const targetOwner = targetOrigin ? seen.get(targetOrigin.selector) : undefined;
    for (const target of targetOwner?.locations?.filter(item => item.pointer.startsWith(targetOwner.pointer + '/targets/')) ?? []) {
      relationships.push({ from: row.selector, to: target.selector, kind: 'target', inherited: row.selector !== targetOwner?.selector });
    }
    return entry;
  });
  return { ...base, entries, relationships };
}

export function sampleRoutesMatch(base: Catalog, current: Catalog) {
  // The hosted dry runs were computed by Python for the published sample.
  const transferFields = (catalog: Catalog) => catalog.entries.map(row => {
    const { title: _title, description: _description, ...fields } = row.entity;
    return fields;
  });
  const canonical = (catalog: Catalog) => JSON.stringify(transferFields(catalog), (_, value: unknown) =>
    isRecord(value) ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
  return canonical(base) === canonical(current);
}
