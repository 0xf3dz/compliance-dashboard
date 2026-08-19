import { onMounted, ref, type Ref } from "vue";

export interface Resource<T> {
  data: Ref<T | null>;
  error: Ref<string | null>;
  loading: Ref<boolean>;
}

// Fetch once on mount, exposing loading / error / data so every panel renders
// a consistent empty-or-error state instead of crashing on fetch failure.
export function useResource<T>(loader: () => Promise<T>): Resource<T> {
  const data = ref<T | null>(null) as Ref<T | null>;
  const error = ref<string | null>(null);
  const loading = ref(true);

  onMounted(async () => {
    try {
      data.value = await loader();
    } catch (err) {
      error.value =
        err instanceof Error ? err.message : "API unavailable";
    } finally {
      loading.value = false;
    }
  });

  return { data, error, loading };
}
