import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsPage } from "../SettingsPage";
import * as api from "@/lib/api";
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
  SIDECAR_BASE: "http://127.0.0.1:8765",
  PrismAPIError: class PrismAPIError extends Error {
    constructor(public status: number, message: string) {
      super(message);
      this.name = "PrismAPIError";
    }
  },
}));

const FAKE_SCHEMAS: ProviderSchema[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    hint: "Best for Chinese, cheap",
    requiresKey: true,
    defaultModel: "deepseek-chat",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "sk-…" },
      { name: "model", label: "Model", required: false, default: "deepseek-chat" },
    ],
  },
  {
    id: "openai",
    label: "OpenAI",
    hint: "GPT-4o / GPT-4o-mini",
    requiresKey: true,
    defaultModel: "gpt-4o-mini",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "sk-…" },
      { name: "model", label: "Model", required: false, default: "gpt-4o-mini" },
    ],
  },
  {
    id: "anthropic",
    label: "Anthropic",
    hint: "Claude 3.5 Sonnet",
    requiresKey: true,
    defaultModel: "claude-3-5-sonnet-20241022",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "sk-ant-…" },
      { name: "model", label: "Model", required: false, default: "claude-3-5-sonnet-20241022" },
    ],
  },
  {
    id: "ollama",
    label: "Ollama (local)",
    hint: "Requires a local `ollama serve`",
    requiresKey: false,
    defaultModel: "qwen2.5:7b",
    fields: [
      { name: "base_url", label: "Ollama host", required: false, default: "http://127.0.0.1:11434" },
      { name: "model", label: "Model", required: false, default: "qwen2.5:7b" },
    ],
  },
  {
    id: "custom",
    label: "Custom (OpenAI-compatible)",
    hint: "MiniMax / 智谱 / Moonshot / any OpenAI-compatible endpoint",
    requiresKey: true,
    defaultModel: "",
    fields: [
      { name: "api_key", label: "API key", required: true, placeholder: "sk-…" },
      { name: "base_url", label: "Base URL", required: true, placeholder: "https://api.example.com/v1" },
      { name: "model", label: "Model", required: true },
    ],
  },
];

const FAKE_LLM_CONFIG: LlmConfig = {
  provider: "deepseek",
  configured: true,
  model: "deepseek-chat",
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
  // The 5th option (custom) only exists once the schemas query resolves,
  // so waiting for it is a clean sync barrier.
  await screen.findByRole("option", { name: /Custom/ });
}

describe("SettingsPage — Provider switcher (AiSection)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.api.listProviders).mockResolvedValue(FAKE_SCHEMAS);
    vi.mocked(api.api.getLlmConfig).mockResolvedValue(FAKE_LLM_CONFIG);
    vi.mocked(api.api.setLlmConfig).mockImplementation(async (update) => ({
      provider: update.provider,
      configured: update.provider === "ollama" ? true : Boolean(update.apiKey),
      model: update.model,
      baseUrl: update.baseUrl,
    }));
    vi.mocked(api.api.health).mockResolvedValue({
      ok: true,
      version: "test",
      sourcesCount: 0,
      itemsCount: 0,
      uptimeSec: 0,
    });
    vi.mocked(api.api.getPendingDistillCount).mockResolvedValue({ pending: 0 });
  });

  it("initial render with deepseek shows one API key input and hides host/base-url", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    expect(select).toHaveValue("deepseek");

    // API key is present
    expect(screen.getByTestId("provider-api-key")).toBeInTheDocument();

    // Ollama host is NOT present for deepseek
    expect(screen.queryByTestId("provider-ollama-host")).not.toBeInTheDocument();

    // Base URL is NOT present for deepseek
    expect(screen.queryByTestId("provider-base-url")).not.toBeInTheDocument();

    // No "no key needed" hint
    expect(screen.queryByTestId("provider-no-key-hint")).not.toBeInTheDocument();
  });

  it("switching to ollama hides the API key field and shows host + model", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    fireEvent.change(select, { target: { value: "ollama" } });

    // API key hidden
    await waitFor(() => {
      expect(screen.queryByTestId("provider-api-key")).not.toBeInTheDocument();
    });

    // Ollama host + model appear
    expect(screen.getByTestId("provider-ollama-host")).toBeInTheDocument();
    expect(screen.getByTestId("provider-model")).toBeInTheDocument();

    // Base URL field (custom) is NOT present
    expect(screen.queryByTestId("provider-base-url")).not.toBeInTheDocument();

    // The "no key needed" hint is shown
    expect(screen.getByTestId("provider-no-key-hint")).toBeInTheDocument();
  });

  it("switching to custom shows all three fields: base_url, model, api_key", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");
    fireEvent.change(select, { target: { value: "custom" } });

    expect(screen.getByTestId("provider-base-url")).toBeInTheDocument();
    expect(screen.getByTestId("provider-model")).toBeInTheDocument();
    expect(screen.getByTestId("provider-api-key")).toBeInTheDocument();

    // Ollama host is NOT present for custom
    expect(screen.queryByTestId("provider-ollama-host")).not.toBeInTheDocument();
  });

  it("switching back to deepseek restores the API key input and hides host fields", async () => {
    renderWithQuery(<SettingsPage />);
    await waitForSchemasLoaded();

    const select = screen.getByTestId("provider-select");

    // Go to ollama first
    fireEvent.change(select, { target: { value: "ollama" } });
    await waitFor(() => {
      expect(screen.getByTestId("provider-ollama-host")).toBeInTheDocument();
    });

    // Back to deepseek
    fireEvent.change(select, { target: { value: "deepseek" } });
    await waitFor(() => {
      expect(screen.getByTestId("provider-api-key")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("provider-ollama-host")).not.toBeInTheDocument();
    expect(screen.queryByTestId("provider-base-url")).not.toBeInTheDocument();
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
