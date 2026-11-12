// Locators of the section-experiment screens shared by the mock and the real-backend specs.
import type { Page } from "@playwright/test";

export const plan = (page: Page) => page.locator(".sx-plan");
export const runCount = (page: Page) => page.getByTestId("sx-run-count");
export const part = (page: Page, name: RegExp) => page.locator(".pick", { has: page.getByRole("button", { name }) });
export const idle = (page: Page) => page.locator(".sx-facts .sx-status.is-idle");
export const runRows = (page: Page) => page.locator(".sx-runs tbody tr[data-run]");
export const compared = (page: Page) => page.locator("#sx-compare-title ~ .table-wrap").first().locator("tbody tr");
