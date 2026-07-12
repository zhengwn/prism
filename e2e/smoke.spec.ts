import { test, expect } from "@playwright/test";
import { installSidecarMock } from "./mock-sidecar";

/**
 * Prism frontend smoke E2E (v0.2c).
 *
 * Exercises the real React UI against a mocked sidecar HTTP contract
 * (see mock-sidecar.ts). See playwright.config.ts for the Tauri-shell
 * caveat — these run in Chromium against the Vite dev server, not the
 * native webview.
 */

test.beforeEach(async ({ page }) => {
  await installSidecarMock(page);
});

test("inbox loads and renders distilled items", async ({ page }) => {
  await page.goto("/inbox");

  // Under the pinned en-US locale the UI language is English, so the item
  // list shows `titleEn` (see InboxPage's title picker).
  await expect(page.getByText("A new open-source LLM drops")).toBeVisible();
  await expect(page.getByText("Notes on prompting")).toBeVisible();

  // The manual sync control is present.
  await expect(page.getByTestId("sync-now-button")).toBeVisible();
});

test.describe("Chinese UI", () => {
  // navigator.language starts with "zh" → detectInitialLanguage() returns "zh".
  test.use({ locale: "zh-CN" });

  test("inbox renders the distilled Chinese titles", async ({ page }) => {
    await page.goto("/inbox");

    // The whole point of the distill pipeline: a zh UI reads `titleZh`.
    await expect(page.getByText("一个新的开源大模型发布")).toBeVisible();
    await expect(page.getByText("关于提示词的笔记")).toBeVisible();
  });
});

test("manual sync fires and surfaces a result toast", async ({ page }) => {
  await page.goto("/inbox");

  const syncCall = page.waitForRequest(
    (req) => req.url().endsWith("/api/sync") && req.method() === "POST",
  );
  await page.getByTestId("sync-now-button").click();
  await syncCall;

  // The job resolves to `done` immediately (mock), so the result toast appears.
  await expect(page.getByTestId("sync-toast")).toBeVisible();
});

test("add-source dialog can create an X source with a bridge feed", async ({ page }) => {
  await page.goto("/sources");

  // Existing seed sources render.
  await expect(page.getByText("Hacker News")).toBeVisible();

  await page.getByTestId("add-source-button").click();
  await expect(page.getByTestId("add-source-dialog")).toBeVisible();

  await page.getByTestId("add-source-name").fill("Simon on X");
  await page.getByTestId("add-source-kind").selectOption("x");
  await page
    .getByTestId("add-source-url")
    .fill("https://rsshub.example.com/twitter/user/simonw");

  // Assert the create call carries kind=x and the bridge feed_url config.
  const createReq = page.waitForRequest(
    (req) => req.url().endsWith("/api/sources") && req.method() === "POST",
  );
  await page.getByTestId("add-source-submit").click();
  const req = await createReq;
  const body = JSON.parse(req.postData() || "{}");
  expect(body.kind).toBe("x");
  expect(body.configJson).toMatchObject({
    feed_url: "https://rsshub.example.com/twitter/user/simonw",
  });
});

test("settings shows sidecar version and the restart control", async ({ page }) => {
  await page.goto("/settings");

  // Health version from the mock.
  await expect(page.getByText("0.2.0")).toBeVisible();

  // The v0.2c Apply & Restart Sidecar button is present and enabled.
  const restart = page.getByRole("button", { name: /Restart Sidecar|重启 Sidecar/ });
  await expect(restart).toBeVisible();
  await expect(restart).toBeEnabled();
});

test.describe("command palette (⌘K)", () => {
  test("opens with the shortcut, navigates, and closes on Escape", async ({ page }) => {
    await page.goto("/inbox");

    const palette = page.getByTestId("command-palette");
    await expect(palette).toBeHidden();

    // ControlOrMeta maps to ⌘ on macOS and Ctrl elsewhere — the listener
    // accepts either.
    await page.keyboard.press("ControlOrMeta+KeyK");
    await expect(palette).toBeVisible();

    // Escape closes it.
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();

    // Re-open and jump to the Sources page via a navigation command.
    await page.keyboard.press("ControlOrMeta+KeyK");
    await expect(palette).toBeVisible();
    await page.locator('[data-command-item="nav-sources"]').click();

    await expect(page).toHaveURL(/\/sources$/);
    await expect(palette).toBeHidden();
  });

  test("searches items and jumps to one from another page", async ({ page }) => {
    await page.goto("/sources");

    // The decorative ⌘K chip in the top bar is now a real trigger.
    await page.getByTestId("command-palette-trigger").click();
    const palette = page.getByTestId("command-palette");
    await expect(palette).toBeVisible();

    // Typing runs the FTS search (mock returns the seeded items); pick one.
    await page.getByTestId("command-palette-input").fill("open-source");
    const itemEntry = page.locator('[data-command-item="item-item_1"]');
    await expect(itemEntry).toBeVisible();
    await itemEntry.click();

    // Choosing an item lands on the inbox with that item selected.
    await expect(page).toHaveURL(/\/inbox$/);
    await expect(palette).toBeHidden();
  });
});
