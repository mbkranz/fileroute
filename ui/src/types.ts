export type RecordValue = Record<string, unknown>;

export type Operation = {
  id: string;
  label: string;
  view: string;
  cli: string;
  intent: 'offline' | 'write' | 'preview' | 'mixed' | 'approval';
  summary: string;
};

export type LocationRecord = {
  path: string;
  serviceType?: string;
  authProfile?: string;
  resourcePathTemplate?: string;
};

export type DescriptorOrigin = {
  originDescriptor: string;
  originSelector: string;
  originPointer: string;
  revision: string;
};

export type CatalogEntry = {
  selector: string;
  pointer: string;
  parent: string;
  artifactPath: string | null;
  name: string | null;
  qualifiedName: string;
  kind: 'catalog' | 'resource' | 'location';
  linked?: boolean;
  provider?: string;
  link?: DescriptorOrigin & { entity: RecordValue };
  entity: RecordValue & { title?: string; description?: string; targets?: LocationRecord[] | null };
  effectiveTargets: LocationRecord[];
  targetOrigin?: { descriptor: string; selector: string } | null;
  locations?: CatalogEntry[];
} & DescriptorOrigin;

export type Relationship = { from: string; to: string; kind: 'source' | 'target' | 'contains'; inherited?: boolean };
export type Catalog = { descriptor: string; activeDescriptor: string | null; entries: CatalogEntry[]; relationships: Relationship[] };
export type Result = RecordValue & { kind?: string; title?: string; rows?: RecordValue[]; content?: string };

export type Snapshot = {
  updated: string;
  repository: { integration: string; commit: string; main: string; baseline: string; implementation: string };
  next: { title: string; description: string; commands: string[] };
  parity: { id: string; label: string; cli: string; local: string; gate: string }[];
  tasks: { id: string; title: string; status: string; detail: string; acceptance: string }[];
  evidence: { id: string; layer: string; status: string; detail: string; command: string }[];
  decisions: { id: string; title: string; status: string; detail: string; link: string }[];
  schemaReview: { id: string; status: string; detail: string }[];
  gates: { id: string; title: string; status: string; detail: string }[];
};

export type Fixture = {
  catalog: Catalog;
  authDescriptions: { overview: Result; scopes: Record<string, Result>; urls: Record<string, Result> };
  yaml: Record<string, string>;
  transfers: Record<string, Result>;
  resolutions: Record<string, Result>;
  discovery: Result;
  diagrams: Record<string, Record<string, Result>>;
  profiles: Result;
  profileDetails: Record<string, Result>;
  migration: Result;
  completion: Record<string, Result>;
  help: Record<string, Result>;
};

export type Profile = { version: string; dialect: string; operations: string; catalog: string; auth: string; cli: Record<string, string>; baseline: string };
