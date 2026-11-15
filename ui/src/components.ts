import {
  Activity, ArrowDown, ArrowDownToLine, ArrowDownUp, ArrowLeft, ArrowRight, ArrowUp,
  ArrowUpRight, BookOpen, Box, Braces, Check, ChevronDown, ChevronRight, CircleHelp,
  CodeXml, Command, Copy, Ellipsis, ExternalLink, File, Files, FilePenLine, FileText,
  FlaskConical, Folder, FolderTree, GitBranch, GitCommitHorizontal, Globe, HardDrive,
  History, LayoutDashboard, ListChecks, LockKeyhole, Menu, Network, PanelLeft, Pencil,
  Plus, Radio, RefreshCcw, Route, Search, Settings2, ShieldCheck, SlidersHorizontal, Terminal,
  TriangleAlert, Unplug, Upload, X,
  createIcons,
} from 'lucide';

const icons = {
  Activity, ArrowDown, ArrowDownToLine, ArrowDownUp, ArrowLeft, ArrowRight, ArrowUp,
  ArrowUpRight, BookOpen, Box, Braces, Check, ChevronDown, ChevronRight, CircleHelp,
  CodeXml, Command, Copy, Ellipsis, ExternalLink, File, Files, FilePenLine, FileText,
  FlaskConical, Folder, FolderTree, GitBranch, GitCommitHorizontal, Globe, HardDrive,
  History, LayoutDashboard, ListChecks, LockKeyhole, Menu, Network, PanelLeft, Pencil,
  Plus, Radio, RefreshCcw, Route, Search, Settings2, ShieldCheck, SlidersHorizontal, Terminal,
  TriangleAlert, Unplug, Upload, X,
};

const escapes: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function esc(value: unknown) {
  return String(value ?? '').replace(/[&<>"']/g, character => escapes[character]);
}

export function icon(name: string) {
  return `<i data-lucide="${name}" aria-hidden="true"></i>`;
}

export function refreshIcons(root?: HTMLElement) {
  createIcons({ icons, root: root ?? document.body, attrs: { 'stroke-width': 1.75, 'aria-hidden': 'true' } });
}

export function json(value: unknown) {
  return JSON.stringify(value, null, 2) || '';
}

export function badge(value: string, tone = '') {
  const color = tone || (/passed|verified|offline|ready|complete/i.test(value) && !/unverified|incomplete/i.test(value) ? 'green'
    : /approval|review|in progress|open|pending/i.test(value) ? 'amber' : 'quiet');
  return `<span class="badge ${color}"><span></span>${esc(value)}</span>`;
}

export function button(label: string, action: string, glyph?: string, kind = 'secondary') {
  return `<button type="button" class="btn ${kind}" data-action="${esc(action)}">${glyph ? icon(glyph) : ''}${esc(label)}</button>`;
}

export function link(label: string, href: string, glyph = 'arrow-up-right', css = 'text-link') {
  return `<a class="${css}" href="${esc(href)}" ${href.startsWith('https://') ? 'target="_blank" rel="noopener noreferrer"' : ''}>${esc(label)}${icon(glyph)}</a>`;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function table(headers: string[], rows: string[][], label: string) {
  if (!rows.length) return '<p class="empty">No matching records.</p>';
  return `<div class="table-wrap"><table aria-label="${esc(label)}"><thead><tr>${headers.map(label => `<th scope="col">${esc(label)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

export function codeBlock(value: unknown) {
  return `<pre class="code-block"><code>${esc(typeof value === 'string' ? value : json(value))}</code></pre>`;
}

export function download(filename: string, content: string, mime = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
