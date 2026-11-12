import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import { SLOW_LOGIN_HINT, scheduleSlowHint } from '../src/lib/auth/slow-hint.ts';

test('die Meldung erscheint erst nach der Wartezeit und verschwindet beim Abbruch', () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const seen: (string | null)[] = [];
    const cancel = scheduleSlowHint((m) => seen.push(m), 1000);
    mock.timers.tick(999);
    assert.deepEqual(seen, []);
    mock.timers.tick(1);
    assert.deepEqual(seen, [SLOW_LOGIN_HINT]);
    cancel();
    assert.deepEqual(seen, [SLOW_LOGIN_HINT, null]);
  } finally {
    mock.timers.reset();
  }
});

test('antwortet der Server vor Ablauf, erscheint die Meldung nie', () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  try {
    const seen: (string | null)[] = [];
    const cancel = scheduleSlowHint((m) => seen.push(m), 1000);
    mock.timers.tick(400);
    cancel();
    mock.timers.tick(5000);
    assert.deepEqual(seen, [null]);
  } finally {
    mock.timers.reset();
  }
});
