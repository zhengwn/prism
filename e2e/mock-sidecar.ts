import type { Page, Route } from "@playwright/test";

/**
 * Hermetic sidecar mock for the E2E suite.
 *
 * The frontend talks to the Python sidecar over HTTP at
 * `http://127.0.0.1:8765` (see `src/lib/api.ts`). We intercept every one
 * of those requests with Playwright's routing so the specs run without a
 * real backend, database, or LLM key. Each fixture matches the camelCase
 * shape the sidecar returns (the Pydantic models alias to camelCase).
 *
 * `installSidecarMock(page)` wires the default happy-path fixtures.
 * Individual specs can override a single endpoint afterwards with
 * `page.route(...)` (last matching handler wins in Playwright).
 */

const SIDECAR = "http://127.0.0.1:8765";

export const HEALTH = {
  ok: true,
  version: "0.2.0",
  sourcesCount: 2,
  itemsCount: 3,
  distillerConfigured: true,
  dbPath: "/tmp/prism-e2e.db",
  uptimeSec: 42,
};

export const SOURCES = [
  {
    id: "src_hn",
    name: "Hacker News",
    kind: "rss",
    url: "https://hn.algolia.com/api/v1/search",
    enabled: true,
    lastSyncedAt: "2026-07-10T00:00:00Z",
    lastError: null,
    itemCount: 2,
    configJson: { is_hn_algolia: true },
  },
  {
    id: "src_simonw",
    name: "Simon Willison",
    kind: "blog",
    url: "https://simonwillison.net/atom/everything/",
    enabled: true,
    lastSyncedAt: "2026-07-10T00:00:00Z",
    lastError: null,
    itemCount: 1,
    configJson: {},
  },
];

export const ITEMS = [
  {
    id: "item_1",
    sourceId: "src_hn",
    sourceName: "Hacker News",
    url: "https://example.com/a",
    titleEn: "A new open-source LLM drops",
    titleZh: "一个新的开源大模型发布",
    summaryEn: "Someone released a model.",
    summaryZh: "有人发布了一个模型。",
    keyPointsZh: ["开源", "可商用"],
    tagsZh: ["大模型", "开源"],
    title: "一个新的开源大模型发布",
    summary: "有人发布了一个模型。",
    keyPoints: ["开源", "可商用"],
    tags: ["大模型", "开源"],
    author: "pg",
    publishedAt: "2026-07-09T12:00:00Z",
    fetchedAt: "2026-07-10T00:00:00Z",
    distilledAt: "2026-07-10T00:01:00Z",
    status: "unread",
    contentType: "article",
    metadataJson: { feed_kind: "rss" },
  },
  {
    id: "item_2",
    sourceId: "src_simonw",
    sourceName: "Simon Willison",
    url: "https://example.com/b",
    titleEn: "Notes on prompting",
    titleZh: "关于提示词的笔记",
    summaryZh: "一些提示词技巧。",
    title: "关于提示词的笔记",
    summary: "一些提示词技巧。",
    keyPointsZh: ["技巧"],
    tagsZh: ["提示词"],
    publishedAt: "2026-07-08T12:00:00Z",
    fetchedAt: "2026-07-10T00:00:00Z",
    distilledAt: "2026-07-10T00:01:00Z",
    status: "unread",
    contentType: "article",
    metadataJson: { feed_kind: "rss" },
  },
];

export const PROVIDERS = [
  {
    id: "deepseek",
    label: "DeepSeek",
    hint: "deepseek-chat",
    requiresKey: true,
    defaultModel: "deepseek-chat",
    fields: [],
  },
  {
    id: "minimax",
    label: "MiniMax",
    hint: "M3 — 1M context, OpenAI-compatible",
    requiresKey: true,
    defaultModel: "MiniMax-M3",
    fields: [],
  },
];

export const LLM_CONFIG = {
  provider: "deepseek",
  configured: true,
  keyLast4: "1234",
  keyLength: 35,
  model: "deepseek-chat",
};

const DISTILL_IDLE = {
  isRunning: false,
  pending: 0,
  distilled: 0,
  failed: 0,
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

export async function installSidecarMock(page: Page): Promise<void> {
  // Health.
  await page.route(`${SIDECAR}/health`, (r) => json(r, HEALTH));

  // Sources list + writes (create / patch / delete echo back success).
  await page.route(`${SIDECAR}/api/sources`, (r) => {
    if (r.request().method() === "POST") {
      const body = JSON.parse(r.request().postData() || "{}");
      return json(
        r,
        {
          id: "src_new",
          name: body.name ?? "New",
          kind: body.kind ?? "rss",
          url: body.url ?? "",
          enabled: true,
          itemCount: 0,
          lastError: null,
          configJson: body.configJson ?? {},
        },
        201,
      );
    }
    return json(r, SOURCES);
  });
  await page.route(`${SIDECAR}/api/sources/*`, (r) => json(r, { ...SOURCES[0], id: "src_x" }));

  // Items list (with or without query string).
  await page.route(`${SIDECAR}/api/items**`, (r) => json(r, ITEMS));

  // Sync: return a terminal `done` job so the inbox poll loop exits at once.
  await page.route(`${SIDECAR}/api/sync`, (r) =>
    json(r, {
      jobId: "job_e2e",
      startedAt: "2026-07-10T00:00:00Z",
      finishedAt: "2026-07-10T00:00:05Z",
      status: "done",
      sourcesTotal: 2,
      sourcesDone: 2,
      itemsNew: 1,
      itemsDistilled: 1,
      error: null,
    }),
  );
  await page.route(`${SIDECAR}/api/sync/*`, (r) =>
    json(r, {
      jobId: "job_e2e",
      startedAt: "2026-07-10T00:00:00Z",
      finishedAt: "2026-07-10T00:00:05Z",
      status: "done",
      sourcesTotal: 2,
      sourcesDone: 2,
      itemsNew: 1,
      itemsDistilled: 1,
      error: null,
    }),
  );

  // Settings / providers / distill status.
  await page.route(`${SIDECAR}/api/settings/llm`, (r) => json(r, LLM_CONFIG));
  await page.route(`${SIDECAR}/api/settings/providers`, (r) => json(r, PROVIDERS));
  await page.route(`${SIDECAR}/api/distill/status`, (r) => json(r, DISTILL_IDLE));
  await page.route(`${SIDECAR}/api/distill/pending-count`, (r) => json(r, { pending: 0 }));

  // SSE progress stream — one idle event, then leave it open. The app
  // swallows stream errors, so an eventual close is harmless.
  await page.route(`${SIDECAR}/api/distill/status/stream`, (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: `data: ${JSON.stringify(DISTILL_IDLE)}\n\n`,
    }),
  );
}
