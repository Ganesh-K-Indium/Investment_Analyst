import { create } from 'zustand';
import { settingsApi, type Settings, type SettingsSchema } from '../services/settingsApi';

interface SettingsState {
    schema: SettingsSchema | null;
    saved: Settings;
    effective: Settings;
    loaded: boolean;
    error: string | null;
    load: () => Promise<void>;
    apply: (r: { saved: Settings; effective: Settings }) => void;
}

/** The signed-in user's settings, loaded once and shared (the chat reads sources/streaming from here; the Settings page edits them). */
export const useSettingsStore = create<SettingsState>((set, get) => ({
    schema: null,
    saved: {},
    effective: {},
    loaded: false,
    error: null,
    load: async () => {
        try {
            const [schema, got] = await Promise.all([get().schema ? Promise.resolve(get().schema as SettingsSchema) : settingsApi.schema(), settingsApi.get()]);
            set({ schema, saved: got.saved, effective: got.effective, loaded: true, error: null });
        } catch (e) {
            set({ error: e instanceof Error ? e.message : 'Could not load settings', loaded: true });
        }
    },
    apply: r => set({ saved: r.saved, effective: r.effective }),
}));

/** One effective value with a fallback (settings may not have loaded yet). The fallback is applied OUTSIDE the selector: a selector that
 *  returns a fresh array/object each call (e.g. a literal `[]`) makes the store re-render without end. */
export function useSetting<T>(section: string, key: string, fallback: T): T {
    const value = useSettingsStore(s => s.effective[section]?.[key]) as T | undefined;
    return value ?? fallback;
}
