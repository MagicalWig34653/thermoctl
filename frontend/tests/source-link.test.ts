import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { SOURCE_LABEL, SOURCE_LINK, SOURCE_URL } from '../src/lib/source.ts';

const read = (path: string) => readFileSync(new URL(`../src/lib/${path}`, import.meta.url), 'utf8');

test('Quelltextverweis: feste https-Adresse, neuer Tab, kein Opener', () => {
  assert.equal(SOURCE_URL, 'https://github.com/MagicalWig34653/thermoctl');
  assert.equal(SOURCE_LINK.href, SOURCE_URL);
  assert.equal(SOURCE_LINK.target, '_blank');
  assert.match(SOURCE_LINK.rel, /noopener/);
  assert.match(SOURCE_LINK.rel, /noreferrer/);
  assert.match(SOURCE_LABEL, /AGPL-3\.0/);
});

test('Quelltextverweis steht in Shell, Hilfe und Kiosk (AGPL §13)', () => {
  assert.match(read('components/shell/Shell.svelte'), /<SourceLink/);
  assert.match(read('views/HelpView.svelte'), /<SourceLink/);
  const kiosk = read('kiosk/KioskApp.svelte');
  // Kopfzeile und geöffneter Raum: zwei Stellen
  assert.equal((kiosk.match(/<SourceLink/g) ?? []).length, 2);
  assert.match(read('components/shell/SourceLink.svelte'), /SOURCE_LINK/);
});
