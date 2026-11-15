import Ajv2020 from 'ajv/dist/2020.js';
import type { ErrorObject, ValidateFunction } from 'ajv';
import { button, esc, icon, isRecord, json } from './components';
import type { Operation, RecordValue } from './types';

export type Schema = {
  $id?: string;
  $ref?: string;
  $defs?: Record<string, Schema>;
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  anyOf?: Schema[];
  oneOf?: Schema[];
  allOf?: Schema[];
  if?: Schema;
  then?: Schema;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  minLength?: number;
  maxLength?: number;
  items?: Schema;
  format?: string;
};

const labels: Record<string, string> = {
  descriptor: 'Descriptor path', selector: 'Exact selector', kind: 'Entry type', settings: 'Profile settings',
  config: 'Profile configuration file', name: 'Registered name', parent: 'Parent selector', profile: 'Default profile',
  fields: 'Field changes', extensions: 'Extension fields', unset: 'Omit authored fields', format: 'Output format', output: 'Output path',
  dry_run: 'Preview only', online: 'Verify online', write: 'Write resolved metadata', force: 'Replace existing descriptor',
  revision: 'Reviewed revision', target_path: 'Target descriptor path', job_id: 'Job ID', preview_id: 'Reviewed plan ID',
  result_id: 'Reviewed discovery result ID', oauth_client_secrets: 'OAuth client JSON path', oauth_token_path: 'OAuth token path',
  env_file: 'Environment file path', url: 'Provider URL', auth_mode: 'Microsoft auth mode',
  pathProperties: 'Path properties', resourcePathTemplate: 'Resource path template',
};
const priority = [
  'method', 'descriptor', 'name', 'parent', 'selector', 'direction', 'kind', 'format', 'detail',
  'profile', 'config', 'online', 'provider', 'mode', 'path', 'title', 'description', 'targets', 'sources',
  'entry', 'settings', 'fields', 'extensions', 'output', 'target_path', 'replace', 'force', 'dry_run', 'write', 'revision',
];

function title(key: string) {
  return labels[key] ?? key.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ').replace(/^./, char => char.toUpperCase());
}

function nested(value: RecordValue, path: string) {
  let at: unknown = value;
  for (const part of path.split('.')) at = isRecord(at) ? at[part] : undefined;
  return at;
}

function assign(root: RecordValue, path: string, value: unknown) {
  const parts = path.split('.');
  const key = parts.pop()!;
  if (parts.some(part => ['__proto__', 'constructor', 'prototype'].includes(part))
    || ['__proto__', 'constructor', 'prototype'].includes(key)) return;
  let record = root;
  for (const part of parts) {
    const current = record[part];
    record = isRecord(current) ? current : record[part] = {};
  }
  if (value === undefined) delete record[key];
  else record[key] = value;
}

type Field = { name: string; schema: Schema; required: boolean; depth: number; initial?: unknown; };

export class FormRenderer {
  readonly operations: Map<string, Schema>;
  private readonly args: Map<string, Schema>;
  private readonly validator: ValidateFunction;
  private readonly validation: Ajv2020;
  private readonly schema: Schema;
  private readonly validators = new Map<string, ValidateFunction>();

  constructor(schema: Schema) {
    this.schema = schema;
    this.validation = new Ajv2020({ allErrors: true, strict: false, validateFormats: false,
      useDefaults: false, coerceTypes: false, removeAdditional: false });
    this.validator = this.validation.compile(schema);
    const operations = (schema.anyOf ?? schema.oneOf ?? []).map(branch => {
      const request = this.resolve(branch);
      return [String(request.properties?.operation.const), request] as const;
    });
    this.operations = new Map(operations);
    this.args = new Map(operations.map(([id, request]) => [id, request.properties!.arguments]));
  }

  resolve(schema: Schema): Schema {
    if (!schema.$ref?.startsWith('#/$defs/')) return schema;
    const { $ref, ...annotations } = schema;
    const definition = this.schema.$defs?.[$ref.slice(8)];
    return definition ? { ...this.resolve(definition), ...annotations } : schema;
  }

  schemaFor(id: string) {
    const args = this.args.get(id);
    return args ? { $schema: 'https://json-schema.org/draft/2020-12/schema', ...args, $defs: this.schema.$defs } : null;
  }

  private branchKey(schema: Schema) {
    return ['method', 'kind'].find(key => schema.anyOf?.some(option => this.resolve(option).properties?.[key]?.const !== undefined));
  }

  private definition(node: Schema, values: RecordValue) {
    const schema = this.resolve(node);
    const key = this.branchKey(schema);
    if (!key) return schema;
    const branches = (schema.anyOf ?? []).map(option => this.resolve(option));
    return branches.find(branch => branch.properties?.[key]?.const === (values[key] ?? (key === 'method' ? 'saved' : 'resource'))) ?? branches[0];
  }

  validate(operation: string, args: RecordValue) {
    if (this.validator({ operation, arguments: args })) {
      if (operation === 'update' && !Object.keys(args.fields as RecordValue).length
        && !Object.keys((args.extensions ?? {}) as RecordValue).length && !(args.unset as string[] | undefined)?.length) {
        return ['Provide at least one authored field or extension to change.'];
      }
      return [];
    }
    const raw = this.args.get(operation);
    if (!raw) return ['Select a registered operation.'];
    const schema = this.definition(raw, args);
    const key = operation + ':' + String(args[this.branchKey(this.resolve(raw)) ?? ''] ?? '');
    let validate = this.validators.get(key);
    if (!validate) {
      validate = this.validation.compile({ ...schema, $defs: this.schema.$defs });
      this.validators.set(key, validate);
    }
    validate(args);
    let errors = validate.errors ?? this.validator.errors ?? [];
    const settings = isRecord(args.settings) ? args.settings : null;
    if (settings && errors.some(error => error.instancePath.startsWith('/settings'))) {
      const model = this.schema.$defs?.AuthProfile;
      const branch = model?.oneOf?.find(option => option.properties?.provider.const === settings.provider);
      const mode = settings.mode ?? branch?.properties?.mode.default;
      const conditional = branch?.allOf?.find(option => {
        const field = option.if?.properties?.mode;
        return field && (field.const === mode || field.enum?.includes(mode));
      });
      if (conditional?.then) {
        const key = 'profile:' + String(settings.provider) + ':' + String(mode);
        let profileCheck = this.validators.get(key);
        if (!profileCheck) {
          profileCheck = this.validation.compile({ ...conditional.then, $defs: this.schema.$defs });
          this.validators.set(key, profileCheck);
        }
        profileCheck(settings);
        if (profileCheck.errors) {
          errors = [...errors.filter(error => !error.instancePath.startsWith('/settings')),
            ...profileCheck.errors.map(error => ({ ...error, instancePath: '/settings' + error.instancePath }))];
        }
      }
    }
    const relevant = errors.filter(error => !['if', 'anyOf', 'oneOf'].includes(error.keyword));
    return [...new Set((relevant.length ? relevant : errors).map(error => this.message(error)))].slice(0, 8);
  }

  private message(error: ErrorObject, prefix = '') {
    const tail = error.instancePath.replace(/^\//, '').split('/').filter(Boolean).join('.');
    const missing = error.keyword === 'required' ? String(error.params.missingProperty) : '';
    const path = [prefix, tail, missing].filter(Boolean).join('.');
    return `${path ? path.split('.').map(title).join(' › ') : 'Arguments'} · ${error.message ?? 'is invalid'}`;
  }

  render(operation: Operation, values: RecordValue) {
    const raw = this.args.get(operation.id);
    if (!raw) return '';
    const root = this.definition(raw, values);
    const key = this.branchKey(this.resolve(raw));
    let output = '';
    if (key && root.properties?.[key]) {
      const choices = this.resolve(raw).anyOf?.map(choice => this.resolve(choice).properties?.[key]?.const)
        .filter(value => typeof value === 'string');
      output = this.control({ name: key, schema: { enum: choices }, required: true, depth: 0 },
        {}, values[key] ?? root.properties[key].const, true);
    }
    output += this.fields(root, values, '', 0, operation, values, key);
    return output;
  }

  renderEntry(operation: Operation, kind: string, authored: RecordValue) {
    const schema = this.definition(this.args.get('update')!, { kind });
    const model = this.resolve(schema.properties!.fields);
    const { catalogs: _catalogs, resources: _resources, ...properties } = model.properties ?? {};
    const extensions = Object.fromEntries(Object.entries(authored).filter(([key]) =>
      !Object.hasOwn(model.properties ?? {}, key)));
    return this.fields({ ...model, properties }, authored, 'fields', 1, operation, { fields: authored })
      + this.control({ name: 'extensions', schema: this.resolve(schema.properties!.extensions), required: false, depth: 1 },
        {}, Object.keys(extensions).length ? extensions : undefined, false);
  }

  validateEntry(kind: string, authored: RecordValue) {
    const definitions: Record<string, string> = { resource: 'Resource', catalog: 'Catalog', link: 'CatalogLink', location: 'Location' };
    const key = 'entry:' + kind;
    let validate = this.validators.get(key);
    if (!validate) {
      validate = this.validation.compile({ $ref: '#/$defs/' + definitions[kind], $defs: this.schema.$defs });
      this.validators.set(key, validate);
    }
    return validate(authored) ? [] : (validate.errors ?? []).map(error => this.message(error)).slice(0, 8);
  }

  private fields(schema: Schema, values: RecordValue, prefix: string, depth: number, operation: Operation, root: RecordValue, hiddenKey?: string): string {
    const names = Object.keys(schema.properties ?? {}).sort((a, b) => {
      const rank = (key: string) => priority.includes(key) ? priority.indexOf(key) : priority.length;
      return rank(a) - rank(b) || a.localeCompare(b);
    });
    return names.filter(key => key !== hiddenKey && !(key === 'method' && schema.properties?.method?.const === 'saved')).map(key => {
      const ref = schema.properties![key];
      const name = prefix ? `${prefix}.${key}` : key;
      const field = { name, schema: this.resolve(ref), required: !!schema.required?.includes(key), depth };
      const value = values[key];
      if (key === 'settings' && ref.$ref?.endsWith('/AuthProfile')) {
        return this.authProfile(field, isRecord(value) ? value : {}, operation, root);
      }
      if (key === 'entry' || (key === 'fields' && operation.id === 'update')) {
        const model = this.resolve(ref);
        const changes = key === 'fields';
        const group = this.fields(model, isRecord(value) ? value : {}, name, depth + 1, operation, root);
        return this.group(changes ? 'Field changes' : 'Authored entry', group + (changes ?
          '<p class="form-hint">Enter only the changes you want to apply. Empty controls are omitted.</p>' : ''));
      }
      const dynamic = (name === 'kind' && ['add', 'update'].includes(operation.id));
      return this.control(field, root, value, dynamic);
    }).join('');
  }

  private authProfile(field: Field, value: RecordValue, operation: Operation, root: RecordValue) {
    const variants = field.schema.oneOf ?? [];
    const choices = variants.map(option => option.properties?.provider.const).filter(item => typeof item === 'string');
    const provider = value.provider ?? choices[0];
    const variant = variants.find(option => option.properties?.provider.const === provider);
    const providerField = this.control({ ...field, name: 'settings.provider', schema: { enum: choices }, required: true }, root, provider, true);
    if (!variant) return this.group('Profile settings', providerField);
    const modeSchema = variant.properties!.mode;
    const mode = value.mode ?? modeSchema.default;
    const modeField = this.control({ ...field, name: 'settings.mode', schema: modeSchema, required: false }, root, mode, true);
    const conditional = variant.allOf?.find(branch => {
      const rule = branch.if?.properties?.mode;
      return rule && (rule.const === mode || rule.enum?.includes(mode));
    });
    const extra = conditional?.then;
    const properties = Object.fromEntries(Object.entries(extra?.properties ?? {}).filter(([key]) => key !== 'provider' && key !== 'mode'));
    return this.group('Profile settings', providerField + modeField +
      this.fields({ properties, required: extra?.required }, value, 'settings', field.depth + 1, operation, root));
  }

  private group(label: string, fields: string) {
    return `<fieldset class="form-group"><legend>${esc(label)}</legend><div class="field-grid">${fields}</div></fieldset>`;
  }

  private control(field: Field, root: RecordValue, value: unknown, dynamic: boolean) {
    const { name, schema, required } = field;
    const key = name.split('.').at(-1)!;
    const id = `field-${name.replaceAll('.', '-')}`;
    const valueType = schema.anyOf ? this.resolve(schema.anyOf.find(sub => sub.type !== 'null') ?? schema) : schema;
    const enums = valueType.enum ?? (valueType.const !== undefined ? [valueType.const] : []);
    const nullable = schema.type === 'null' || schema.anyOf?.some(sub => sub.type === 'null');
    const options = enums.filter(option => option !== null);
    const current = value === undefined && field.depth === 0 ? valueType.default : value;
    const scalarText = ['title', 'description', 'path', 'descriptor'].includes(key) && valueType.type === 'string';
    const stringValue = current === undefined || (current === null && !scalarText) ? '' : String(current);
    const controlName = `id="${id}" name="${esc(name)}" data-field="${esc(name)}"`;
    const common = `${controlName}${dynamic ? ' data-dynamic="1"' : ''}`;
    const label = `<label for="${id}">${esc(name === 'settings.provider' ? 'Provider' : title(key))}${required ? '<span class="required" aria-hidden="true"> *</span>' : ''}</label>`;
    if (key === 'targets') {
      const state = value === undefined ? '' : value === null ? 'null' : Array.isArray(value) && value.length === 0 ? 'disabled' : 'custom';
      return `<div class="schema-field full">${label}<select ${common} data-target-state="1">
        <option value="" ${state === '' ? 'selected' : ''}>Omit · inherit</option>
        <option value="null" ${state === 'null' ? 'selected' : ''}>Explicit null · inherit</option>
        <option value="disabled" ${state === 'disabled' ? 'selected' : ''}>Disable publication</option>
        <option value="custom" ${state === 'custom' ? 'selected' : ''}>Set destinations</option></select>
        <textarea data-target-value="${esc(name)}" aria-label="Destinations JSON" placeholder='[{"path":"s3://example-bucket/key"}]' ${state === 'custom' ? '' : 'hidden'}>${state === 'custom' ? esc(json(value)) : ''}</textarea></div>`;
    }
    if (name === 'method' && schema.const === 'saved') return '';
    if (key === 'revision') {
      return `<input type="hidden" ${common} value="${esc(stringValue)}" />`;
    }
    if (options.length) {
      const items = options.map(option => `<option value="${esc(option)}" ${String(option) === stringValue ? 'selected' : ''}>${esc(option)}</option>`).join('');
      const prompt = required ? 'Choose an option' : current === undefined ? 'Omit' : 'Use default';
      return `<div class="schema-field">${label}<select ${common} data-enum="${esc(json(options))}">${!required || !stringValue ? `<option value="">${prompt}</option>` : ''}${items}${nullable && !dynamic ? '<option value="__null__">Explicit null</option>' : ''}</select></div>`;
    }
    if (valueType.type === 'boolean' || valueType.const === true) {
      if (nullable || name.startsWith('fields.')) {
        return `<div class="schema-field">${label}<select ${common} data-boolean="1"><option value="">Omit</option><option value="true" ${value === true ? 'selected' : ''}>Yes</option>${valueType.const === true ? '' : `<option value="false" ${value === false ? 'selected' : ''}>No</option>`}${nullable ? `<option value="__null__" ${value === null ? 'selected' : ''}>Explicit null</option>` : ''}</select></div>`;
      }
      return `<div class="schema-field toggle-field"><label><input type="checkbox" ${common} ${current ? 'checked' : ''} /><span>${esc(title(key))}</span></label>${key === 'online' ? '<span class="form-hint">Requires explicit local approval.</span>' : ''}</div>`;
    }
    const parent = root[name.split('.')[0]];
    if (valueType.type === 'array' || valueType.type === 'object' || (!scalarText && schema.anyOf) || schema.oneOf) {
      const initial = value === undefined ? '' : json(value);
      const example = valueType.type === 'array' ? '[]' : valueType.type === 'object' ? '{}' : 'JSON value';
      return `<div class="schema-field full">${label}<textarea ${common} data-json="1" rows="3" spellcheck="false" placeholder="${esc(example)}">${esc(initial)}</textarea>${field.depth === 0 ? '<span class="form-hint">Use JSON.</span>' : ''}</div>`;
    }
    const reference = key.includes('file') || key.includes('path') || key === 'selector' || key === 'descriptor';
    return `<div class="schema-field">${label}<input ${common} ${reference ? 'class="mono"' : ''} type="${['integer', 'number'].includes(String(valueType.type)) ? 'number' : 'text'}" value="${esc(stringValue)}"
      ${valueType.maxLength ? `maxlength="${valueType.maxLength}"` : ''} ${nullable ? 'data-nullable="1"' : ''} placeholder="${esc(key === 'descriptor' ? 'Use open descriptor' : key === 'selector' ? 'Whole descriptor' : '')}" />
      ${nullable && (!parent || scalarText) ? '<span class="form-hint">Type null to send explicit null.</span>' : ''}</div>`;
  }

  read(form: HTMLElement) {
    const argumentsValue: RecordValue = {};
    const errors: string[] = [];
    const controls = form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('[data-field]');
    for (const input of controls) {
      const name = input.dataset.field!;
      const data = input.dataset;
      let value: unknown = input.value;
      if (data.targetState) {
        value = input.value === 'disabled' ? [] : input.value === 'null' ? null : undefined;
        if (input.value === 'custom') {
          const textarea = form.querySelector<HTMLTextAreaElement>(`[data-target-value="${name}"]`)!;
          try { value = JSON.parse(textarea.value); }
          catch { errors.push('Targets · enter an array of destination objects.'); }
        }
      } else if (input instanceof HTMLInputElement && input.type === 'checkbox') value = input.checked;
      else if (input.value === '') value = undefined;
      else if (input.value === '__null__' || (data.nullable && input.value === 'null')) value = null;
      else if (data.boolean) value = input.value === 'true';
      else if (data.enum) value = (JSON.parse(data.enum) as unknown[]).find(option => String(option) === input.value);
      else if (data.json) {
        try { value = JSON.parse(input.value); }
        catch { errors.push(`${name.split('.').map(title).join(' › ')} · enter valid JSON.`); value = undefined; }
      }
      else if (input instanceof HTMLInputElement && input.type === 'number') value = input.valueAsNumber;
      assign(argumentsValue, name, value);
    }
    if (isRecord(argumentsValue.entry) && Object.values(argumentsValue.entry).every(value => value === undefined)) argumentsValue.entry = {};
    if (form.dataset.operationForm === 'update' && !isRecord(argumentsValue.fields)) argumentsValue.fields = {};
    if (form.dataset.operationForm === 'add' && !isRecord(argumentsValue.entry)) argumentsValue.entry = {};
    if (form.dataset.operationForm === 'auth.login.s3') argumentsValue.method = 'saved';
    return { args: argumentsValue, errors };
  }

  actions(operation: Operation, local: boolean) {
    const text = operation.id === 'approval.request' ? 'Review approval' : operation.intent === 'approval'
      ? (local ? 'Review request' : 'Review offline') : operation.intent === 'write'
        ? (local ? 'Review changes' : 'Review offline') : 'Show preview';
    return `<div class="form-actions"><button class="btn primary" type="submit">${icon(operation.intent === 'approval' ? 'shield-check' : 'arrow-right')}${text}</button>
      ${button('Copy request', 'copy-request', 'copy', 'ghost')}</div>`;
  }
}
