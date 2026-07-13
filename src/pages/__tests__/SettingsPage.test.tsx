import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsPage } from "../SettingsPage";
import { usePrismStore } from "@/store";
import * as api from "@/lib/api";
import * as notifications from "@/lib/notifications";
import type { LlmConfig, ProviderSchema } from "@/types";

// `useTheme` calls `window.matchMedia` for the system-mode live listener.
// jsdom doesn't ship one; mock it before any module that touches the hook
// imports. The handler is a no-op — we don't assert on theme state, just on
// the AI section rendering.
beforeAll(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
});

vi.mock("@/lib/api", () => ({
  api: {
    health: vi.fn(),
    listProviders: vi.fn(),
    getLlmConfig: vi.fn(),
    setLlmConfig: vi.fn(),
    getPendingDistillCount: vi.fn(),
    redistill: vi.fn(),
    syncAll: vi.fn(),
  },
  isTauri: () => false,
  SIDECAR_BASE: "http://127.0.0.1:8765",
  PrismAPIError: class PrismAPIError extends Error {
    constructor(public status: number, message: string) {
      super(message);
      this.name = "PrismAPIError";
    }
  },
}));

// Keep the real localStorage-backed getStored/setStored (the store imports
// them); stub the permission request + notify.
vi.mock("@/lib/notifications", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/notifications")>();
  return { ...actual, ensureNotificationPermission: vi.fn(), notify: vi.fn() };
});

// v0.2a+: only 2 providers, both key-required. `defaultModel` carries
// the USER-FACING model id (no litellm routing prefix) — the distiller
// prepends the prefix internally.
const FAKE_SCHEMAS: ProviderSchema[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    hint: "Best for Chinese, cheap",
    requiresKey: true,
    defaultModel: "deepseek-v4-pro",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "sk-…" },
    ],
  },
  {
    id: "minimax",
    label: "MiniMax",
    hint: "M3 — 1M context, OpenAI-compatible",
    requiresKey: true,
    defaultModel: "MiniMax-M3",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "ey…" },
    ],
  },
];

const FAKE_LLM_CONFIG: LlmConfig = {
  provider: "deepseek",
  configured: true,
  // Simulate the trailing-4 + length surface the Tauri sidecar ships:
  // real builds populate both together from the keystore decrypt.
  keyLast4: "1dh4",
  keyLength: 35,
  model: "deepseek-v4-pro",
};

function renderWithQuery(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

/**
 * Wait until the provider dropdown has finished hydrating from React Query:
 * the schemas query must have resolved, the config query must have resolved,
 * and the controlled `<select>` must reflect the active provider. Without
 * this, assertions on the field set can race the initial render and see
 * a half-rendered (no options, no apiKey) form.
 */
async function waitForSchemasLoaded() {
  // The 2nd option (minimax) only exists once the schemas query resolves,
  // so waiting for it is a clean sync barrier.
  await screen.findByRole("option", { name: /MiniMax/ });
}

describe("SettingsPage — Provider switcher (AiSection)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listProviders).mockResolvedValue(FAKE_SCHEMAS);
    vi.mocked(api.api.getLlmConfig).mockResolvedValue(FAKE_LLM_CONFIG);
    vi.mocked(api.api.setLlmConfig).mockImplementation(async (update) => ({
      provider: update.provider,
      configured: Boolean(update.apiKey),
      model: update.model,
      baseUrl: update.baseUrl,
    }));
    vi.mocked(api.api.health).mockResolvedValue({
      ok: true,
      version: "test",
      sourcesCount: 0,
      itemsCount: 0,
      distillerConfigured: false,
      dbPath: "/tmp/test.db",
      uptimeSec: 0,
    });
    vi.mocked(api.api.getPendingDistillCount).mockResolvedValue({ pending: 0 });
  });

  it("initial render with deepseek shows the API key input and the inline model field", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    expect(select).toHaveValue("deepseek");

    // API key is present
    expect(screen.getByTestId("provider-api-key")).toBeInTheDocument();

    // Model field is inline (no "Advanced" disclosure in v0.2a+).
    const model = screen.getByTestId("provider-model");
    expect(model).toBeInTheDocument();
    // The placeholder should carry the user-facing model id (no
    // litellm routing prefix leaking into the UI).
    expect(model).toHaveAttribute("placeholder", "deepseek-v4-pro");

    // v0.2a+: no ollama / base-url / no-key-hint / advanced-disclosure
    expect(screen.queryByTestId("provider-ollama-host")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-base-url")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-no-key-hint")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-advanced")).not.toBeInTheDocument();
  });

  it("switching to minimax updates the model placeholder to 'M3' (user-facing id)", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    fireEvent.change(select, { target: { value: "minimax" } });

    // Both providers need a key, so the api-key field stays.
    expect(screen.getByTestId("provider-api-key")).toBeInTheDocument();

    // Model field stays inline. Placeholder switches to the user-facing
    // MiniMax id "MiniMax-M3" — the litellm "openai/" prefix must NOT
    // leak into the UI placeholder.
    const model = screen.getByTestId("provider-model");
    expect(model).toHaveAttribute("placeholder", "MiniMax-M3");
  });

  it("switching back to deepseek restores the 'deepseek-v4-pro' placeholder", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    fireEvent.change(select, { target: { value: "minimax" } });
    fireEvent.change(select, { target: { value: "deepseek" } });

    const model = screen.getByTestId("provider-model");
    expect(model).toHaveAttribute("placeholder", "deepseek-v4-pro");
  });

  it("clicking save calls api.setLlmConfig with provider and the typed apiKey", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    // Type into the API key field while still on deepseek
    const keyInput = screen.getByTestId("provider-api-key");
    fireEvent.change(keyInput, { target: { value: "sk-test-1234" } });

    // Click save
    fireEvent.click(screen.getByTestId("provider-save"));

    await waitFor(() => {
      expect(api.api.setLlmConfig).toHaveBeenCalled();
    });

    const call = vi.mocked(api.api.setLlmConfig).mock.calls[0]?.[0];
    expect(call).toBeDefined();
    expect(call!.provider).toBe("deepseek");
    expect(call!.apiKey).toBe("sk-test-1234");
  });
});

describe("SettingsPage — Notifications card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listProviders).mockResolvedValue(FAKE_SCHEMAS);
    vi.mocked(api.api.getLlmConfig).mockResolvedValue(FAKE_LLM_CONFIG);
    vi.mocked(api.api.health).mockResolvedValue(undefined as never);
    vi.mocked(api.api.getPendingDistillCount).mockResolvedValue({ pending: 0 });
    usePrismStore.setState({ notificationsEnabled: false });
    localStorage.clear();
  });

  it("enables notifications when permission is granted", async () => {
    vi.mocked(notifications.ensureNotificationPermission).mockResolvedValue(true);
    renderWithQuery(<SettingsPage />);

    const toggle = await screen.findByTestId("notifications-toggle");
    expect(toggle).toHaveAttribute("data-enabled", "false");

    fireEvent.click(toggle);

    await waitFor(() => expect(notifications.ensureNotificationPermission).toHaveBeenCalled());
    await waitFor(() => expect(usePrismStore.getState().notificationsEnabled).toBe(true));
    expect(toggle).toHaveAttribute("data-enabled", "true");
  });

  it("stays off and shows a hint when permission is denied", async () => {
    vi.mocked(notifications.ensureNotificationPermission).mockResolvedValue(false);
    renderWithQuery(<SettingsPage />);

    const toggle = await screen.findByTestId("notifications-toggle");
    fireEvent.click(toggle);

    await waitFor(() => expect(screen.getByTestId("notifications-denied")).toBeInTheDocument());
    expect(usePrismStore.getState().notificationsEnabled).toBe(false);
    expect(toggle).toHaveAttribute("data-enabled", "false");
  });
});
