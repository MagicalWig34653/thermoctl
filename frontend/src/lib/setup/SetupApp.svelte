<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { Thermometer } from '@lucide/svelte';
  import { createApi, runtimePrefix } from '$lib/api';
  import { createAppearance } from '$lib/appearance.svelte';
  import { appHref } from '$lib/paths';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { Label } from '$lib/components/ui/label';
  import {
    LIMITS,
    loadSetupStatus,
    submitSetup,
    suggestedTimezone,
    validateLocal,
    type FieldErrors,
    type SetupField,
    type SetupForm,
    type SetupStatus,
  } from './client';

  // Eigenständig: keine App-Hülle, kein Bootstrap, keine Sitzung -- es gibt noch keinen Benutzer.
  const prefix = runtimePrefix();
  createAppearance();
  // Keine Sitzung, die ablaufen könnte: 401/„veraltet“ lösen hier nichts aus.
  const api = createApi({ fetch: window.fetch.bind(window), prefix, cookies: () => document.cookie });
  const loginHref = appHref(prefix, '/login');

  let status = $state<SetupStatus | 'loading' | 'done'>('loading');
  let form = $state<SetupForm>({
    setup_token: '',
    username: '',
    display_name: '',
    password: '',
    timezone: suggestedTimezone(),
  });
  let errors = $state<FieldErrors>({});
  let failure = $state<string | null>(null);
  let pending = $state(false);

  onMount(() => {
    document.title = 'Einrichtung – thermoctl';
    void loadSetupStatus(api).then((s) => (status = s));
  });

  async function focusFirstError(fields: FieldErrors) {
    await tick();
    const first = (['setup_token', 'username', 'display_name', 'password', 'timezone'] as SetupField[]).find(
      (f) => fields[f],
    );
    if (first) document.getElementById(`setup-${first}`)?.focus();
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (pending) return;
    failure = null;
    const local = validateLocal(form);
    errors = local;
    if (Object.keys(local).length > 0) {
      await focusFirstError(local);
      return;
    }
    pending = true;
    const outcome = await submitSetup(api, form);
    pending = false;
    // Das Passwort bleibt nach keinem Versuch im Formular.
    form.password = '';
    if (outcome.kind === 'done') {
      status = 'done';
    } else if (outcome.kind === 'closed') {
      status = 'closed';
    } else if (outcome.kind === 'field') {
      errors = { [outcome.field]: outcome.message };
      await focusFirstError(errors);
    } else {
      failure = outcome.message;
    }
  }

  const FIELD_LABELS: { id: SetupField; label: string; type?: string; autocomplete?: string; max?: number; hint?: string }[] = [
    { id: 'setup_token', label: 'Einrichtungs-Token', autocomplete: 'off', hint: 'Steht einmalig im Protokoll des Dienstes und ist nur einmal verwendbar.' },
    { id: 'username', label: 'Benutzername', autocomplete: 'username', max: LIMITS.username },
    { id: 'display_name', label: 'Anzeigename', autocomplete: 'name', max: LIMITS.display_name },
    { id: 'password', label: 'Passwort', type: 'password', autocomplete: 'new-password' },
    { id: 'timezone', label: 'Zeitzone', autocomplete: 'off', max: LIMITS.timezone },
  ];
</script>

<main class="grid min-h-screen place-items-center px-4 py-10">
  <div class="w-full max-w-md">
    <div class="mb-6 flex items-center gap-3">
      <div class="grid size-10 place-items-center rounded-lg border bg-background">
        <Thermometer size={22} aria-hidden="true" />
      </div>
      <div>
        <h1 class="m-0 text-2xl">thermoctl</h1>
        <p class="m-0 text-sm text-muted-foreground">Einrichtung</p>
      </div>
    </div>

    {#if status === 'loading'}
      <p class="text-muted-foreground" role="status">Lade …</p>
    {:else if status === 'error'}
      <div class="rounded-xl border bg-card p-6 shadow-sm">
        <h2 class="mb-2">Keine Verbindung</h2>
        <p class="text-sm text-destructive" role="alert">Der Einrichtungsstatus konnte nicht gelesen werden.</p>
        <Button class="mt-4" onclick={() => location.reload()}>Erneut versuchen</Button>
      </div>
    {:else if status === 'closed'}
      <div class="rounded-xl border bg-card p-6 shadow-sm">
        <h2 class="mb-2">Bereits eingerichtet</h2>
        <p class="text-sm text-muted-foreground">Die Einrichtung ist abgeschlossen und dauerhaft geschlossen.</p>
        <Button class="mt-4 w-full" href={loginHref}>Zur Anmeldung</Button>
      </div>
    {:else if status === 'done'}
      <div class="rounded-xl border bg-card p-6 shadow-sm">
        <h2 class="mb-2">Einrichtung abgeschlossen</h2>
        <p class="text-sm text-muted-foreground" role="status">
          Der erste Verwalter ist angelegt. Du kannst dich jetzt anmelden.
        </p>
        <Button class="mt-4 w-full" href={loginHref}>Zur Anmeldung</Button>
      </div>
    {:else}
      <form class="rounded-xl border bg-card p-6 shadow-sm" onsubmit={submit} novalidate>
        <h2 class="mb-1">Ersteinrichtung</h2>
        <p class="mb-2 text-sm text-muted-foreground">
          Legt den ersten Verwalter an. Die Einrichtung ist nur erreichbar, solange noch kein Benutzer
          existiert, und danach dauerhaft geschlossen.
        </p>
        {#each FIELD_LABELS as f (f.id)}
          <div class="mt-4 flex flex-col gap-2">
            <Label for={`setup-${f.id}`}>{f.label}</Label>
            <Input
              id={`setup-${f.id}`}
              name={f.id}
              type={f.type}
              autocomplete={f.autocomplete as never}
              autocapitalize="none"
              spellcheck="false"
              maxlength={f.max}
              required
              bind:value={form[f.id]}
              aria-invalid={errors[f.id] ? 'true' : undefined}
              aria-describedby={errors[f.id] ? `setup-${f.id}-error` : f.hint ? `setup-${f.id}-hint` : undefined}
            />
            {#if errors[f.id]}
              <p id={`setup-${f.id}-error`} class="text-sm text-destructive" role="alert">{errors[f.id]}</p>
            {:else if f.hint}
              <p id={`setup-${f.id}-hint`} class="text-sm text-muted-foreground">{f.hint}</p>
            {/if}
          </div>
        {/each}
        {#if failure}
          <p class="mt-4 text-sm text-destructive" role="alert">{failure}</p>
        {/if}
        <Button type="submit" class="mt-5 w-full" disabled={pending}>
          {pending ? 'Wird eingerichtet …' : 'Einrichtung abschließen'}
        </Button>
      </form>
    {/if}
  </div>
</main>
