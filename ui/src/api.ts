import type { Snapshot } from "./model";

type Host = {
  command(action: string, payload: Record<string, unknown>): Promise<Snapshot>;
  close(): Promise<void>;
  ready(): Promise<{ mode: string }>;
};
declare global {
  interface Window {
    pywebview?: { api: Host };
  }
}
export async function host(): Promise<Host> {
  if (window.pywebview?.api) return window.pywebview.api;
  return new Promise((resolve) =>
    window.addEventListener(
      "pywebviewready",
      () => resolve(window.pywebview!.api),
      { once: true },
    ),
  );
}
export const request = async (
  action: string,
  payload: Record<string, unknown> = {},
) => (await host()).command(action, payload);
