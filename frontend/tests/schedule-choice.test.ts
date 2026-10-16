import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scheduleZoneId } from '../src/lib/schedule/choice';

const zones = [
  { id: 1, can_edit_schedule: false },
  { id: 2, can_edit_schedule: true },
  { id: 3, can_edit_schedule: true },
];

test('without a choice the first room the user may edit is preselected', () => {
  assert.equal(scheduleZoneId(zones, null), 2);
});

test('a valid choice of the user wins, even for a read-only room', () => {
  assert.equal(scheduleZoneId(zones, 1), 1);
  assert.equal(scheduleZoneId(zones, 3), 3);
});

test('a choice that is no longer visible falls back to the first editable room', () => {
  assert.equal(scheduleZoneId(zones, 99), 2);
});

test('without any editable room the first readable room is shown', () => {
  assert.equal(scheduleZoneId([{ id: 5 }, { id: 6, can_edit_schedule: false }], null), 5);
});

test('an older server without the flag behaves as before', () => {
  assert.equal(scheduleZoneId([{ id: 7 }, { id: 8 }], null), 7);
});

test('no rooms at all gives no room', () => {
  assert.equal(scheduleZoneId([], null), null);
  assert.equal(scheduleZoneId([], 4), null);
});
