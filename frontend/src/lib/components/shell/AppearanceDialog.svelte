<script lang="ts">
  import { Check, Monitor, Moon, Sun } from '@lucide/svelte';
  import * as Dialog from '$lib/components/ui/dialog';
  import { ACCENTS, type Theme } from '$lib/appearance.svelte';
  import { useApp } from '$lib/context';

  let { open = $bindable(false) }: { open: boolean } = $props();
  const { appearance } = useApp();

  const themes: { id: Theme; label: string; icon: typeof Sun }[] = [
    { id: 'system', label: 'System', icon: Monitor },
    { id: 'light', label: 'Hell', icon: Sun },
    { id: 'dark', label: 'Dunkel', icon: Moon },
  ];
  const isPreset = $derived(ACCENTS.some((a) => a.hex === appearance.accent));
</script>

<Dialog.Root bind:open>
  <Dialog.Content class="sm:max-w-md">
    <Dialog.Header>
      <Dialog.Title>Darstellung</Dialog.Title>
      <Dialog.Description>Gilt nur in diesem Browser.</Dialog.Description>
    </Dialog.Header>

    <fieldset class="m-0 border-0 p-0">
      <legend class="mb-2 text-sm font-medium">Farbschema</legend>
      <div class="grid grid-cols-3 gap-2">
        {#each themes as t (t.id)}
          <button
            type="button"
            class="flex min-h-11 items-center justify-center gap-2 rounded-md border px-3 text-sm hover:bg-muted aria-pressed:border-brand aria-pressed:bg-brand-soft"
            aria-pressed={appearance.theme === t.id}
            onclick={() => appearance.setTheme(t.id)}
          >
            <t.icon size={16} aria-hidden="true" />{t.label}
          </button>
        {/each}
      </div>
    </fieldset>

    <fieldset class="m-0 border-0 p-0">
      <legend class="mb-2 text-sm font-medium">Akzentfarbe</legend>
      <div class="flex flex-wrap items-center gap-2">
        {#each ACCENTS as a (a.id)}
          <button
            type="button"
            class="grid size-11 place-items-center rounded-full border-2 border-transparent aria-pressed:border-foreground"
            style:background-color={a.hex}
            aria-label={a.label}
            aria-pressed={appearance.accent === a.hex}
            onclick={() => appearance.setAccent(a.hex)}
          >
            {#if appearance.accent === a.hex}<Check size={18} color="#fff" aria-hidden="true" />{/if}
          </button>
        {/each}
        <label class="ml-2 flex items-center gap-2 text-sm">
          Eigene
          <input
            type="color"
            class="h-11 w-14 cursor-pointer rounded-md border bg-transparent p-1"
            value={appearance.accent}
            aria-label="Eigene Akzentfarbe"
            oninput={(e) => appearance.setAccent(e.currentTarget.value)}
          />
        </label>
      </div>
      {#if !isPreset}<p class="mt-2 text-xs text-muted-foreground">Eigene Farbe aktiv.</p>{/if}
    </fieldset>
  </Dialog.Content>
</Dialog.Root>
