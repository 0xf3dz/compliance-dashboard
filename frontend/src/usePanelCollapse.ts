import { ref } from "vue";

// Each panel folds under its heading, and the fold survives a refresh. The
// state lives in localStorage under one key per panel, so the dashboard opens
// the way the reader left it. A panel with no stored choice starts collapsed.
export function usePanelCollapse(name: string) {
  const key = `panel:${name}`;
  const open = ref(localStorage.getItem(key) === "1");

  // The toggle event fires after the browser flips the open property, so the
  // listener reads the new state and stores it. The :open binding restores it.
  function persist(event: Event) {
    localStorage.setItem(key, (event.target as HTMLDetailsElement).open ? "1" : "0");
  }

  return { open, persist };
}
