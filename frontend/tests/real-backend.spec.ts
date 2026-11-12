// End-to-end against the real backend in fixture mode (playwright.config.ts, project "real"): a generated clip is
// uploaded and goes through question → review → audit → render with the scripted models. Every upload yields one
// person (face + body for the whole clip) and one name tag, one question, and a plan of D-1 (mosaic over the face)
// and D-2 (mosaic over the name tag). A clip whose file carries a shooting-location tag also gets D-3, which
// removes that tag from the output file.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

let clip = "";
/** the same clip with a shooting-location (GPS) tag in the file */
let located = "";
let caseId = "";

test.beforeAll(() => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "privframe-e2e-clip-"));
  const make = (name: string, extra: string[]) => {
    const file = path.join(dir, name);
    execFileSync(
      "ffmpeg",
      ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=4:size=640x360:rate=24", "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", "-pix_fmt", "yuv420p", ...extra, file],
      { stdio: "pipe" },
    );
    return file;
  };
  clip = make("clip.mp4", []);
  located = make("located.mp4", ["-metadata", "location=+37.5665+126.9780/"]);
});

test.afterAll(() => {
  if (clip) fs.rmSync(path.dirname(clip), { recursive: true, force: true });
});

/** the player has a frame to show: the proxy video decoded, or (a browser without the codec) a still */
async function frameReady(page: Page): Promise<boolean> {
  return page.locator(".player-view").evaluate((view) => {
    const video = view.querySelector("video");
    if (video) return video.readyState >= 2 && video.videoWidth > 0;
    const img = view.querySelector("img");
    return !!img && img.complete && img.naturalWidth > 0;
  });
}

/**
 * How far the plan preview is from the bare picture along a horizontal line through the face of person-1: the
 * largest difference (0–255) between the preview canvas and the frame it was painted from; -1 before anything is
 * drawn. The clip has a hard blue|yellow edge at x = 0.5, right through the face (around (0.5, 0.32)): a mosaic
 * turns the cell that holds the edge into their mean, far from both, and a kept face is the picture itself.
 */
async function redaction(page: Page): Promise<number> {
  return page.locator(".player-view").evaluate((view) => {
    const overlay = view.querySelector<HTMLCanvasElement>(".player-overlay");
    const source = view.querySelector<HTMLVideoElement | HTMLImageElement>("video, img");
    const ctx = overlay?.getContext("2d");
    if (!overlay || !source || !ctx || overlay.width === 0) return -1;
    const y = Math.round(0.32 * overlay.height);
    const x = Math.round(0.42 * overlay.width);
    const width = Math.round(0.16 * overlay.width);
    const painted = ctx.getImageData(x, y, width, 1).data;
    if (painted[3] === 0) return -1;
    const bare = document.createElement("canvas");
    bare.width = overlay.width;
    bare.height = overlay.height;
    const bareCtx = bare.getContext("2d");
    if (!bareCtx) return -1;
    bareCtx.imageSmoothingQuality = "high";
    bareCtx.drawImage(source, 0, 0, bare.width, bare.height);
    const picture = bareCtx.getImageData(x, y, width, 1).data;
    let furthest = 0;
    for (let i = 0; i < painted.length; i += 4) {
      furthest = Math.max(furthest, Math.abs(painted[i] - picture[i]), Math.abs(painted[i + 1] - picture[i + 1]), Math.abs(painted[i + 2] - picture[i + 2]));
    }
    return furthest;
  });
}

/** upload a clip, answer the analyst's one question ("public") and wait for the review screen with a frame in the player */
async function uploadToReview(page: Page, file: string): Promise<string> {
  await page.goto("/");
  await page.locator('input[type="file"]').setInputFiles(file);
  await expect(page.locator(".source-name")).toHaveText(path.basename(file));
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "분석 시작" }).click();
  await expect(page).toHaveURL(/\/cases\/case_/, { timeout: 60_000 });
  const id = new URL(page.url()).pathname.split("/").pop() ?? "";
  await expect(page.getByRole("heading", { name: /판단에 필요한 사실 1가지/ })).toBeVisible({ timeout: 60_000 });
  await page.locator('input[name="q-Q1"][value="public"]').check();
  await page.getByRole("button", { name: /답변 보내기/ }).click();
  await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });
  await expect.poll(() => frameReady(page), { timeout: 30_000 }).toBe(true);
  return id;
}

const methods = (page: Page, id: string) => page.locator(`input[name="method-${id}"]`).evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value));

test.describe("one run, from upload to the Lab", () => {
  // the second test opens the run the first one made
  test.describe.configure({ mode: "serial" });

  test("an uploaded video goes from analysis to a verified output that can be downloaded", async ({ page }) => {
    test.setTimeout(240_000);
    await page.goto("/");
    // honest labels: nothing here is mock data, and this server has no example video to offer
    await expect(page.getByText(/1분 영상 기준 추정 비용/)).toBeVisible(); // shown once the backend has said it is real
    await expect(page.locator(".mock-badge")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /예시 영상/ })).toHaveCount(0);

    await page.locator('input[type="file"]').setInputFiles(clip);
    await expect(page.locator(".source-name")).toHaveText("clip.mp4");
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "분석 시작" }).click();
    await expect(page).toHaveURL(/\/cases\/case_/, { timeout: 60_000 });
    caseId = new URL(page.url()).pathname.split("/").pop() ?? "";

    // the analyst asks its one question, next to a real frame of the video with the person it is about outlined
    await expect(page.getByRole("heading", { name: /판단에 필요한 사실 1가지/ })).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".qcard-frame img")).toBeVisible();
    await expect(page.locator(".qcard-frame .still-mark")).toHaveCount(1);
    await page.locator('input[name="q-Q1"][value="public"]').check();
    await page.getByRole("button", { name: /답변 보내기/ }).click();

    // review: the real player with the plan drawn over it, and both decisions
    await expect(page.getByRole("button", { name: "감사 요청" })).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".player-view video, .player-view img")).toBeVisible();
    await expect.poll(() => frameReady(page), { timeout: 30_000 }).toBe(true);
    await expect(page.locator(".scene")).toHaveCount(0); // not the mock's illustration
    await expect(page.locator(".dcard")).toHaveCount(2);
    // something on screen is kept or covered with a mosaic, and nothing else is offered
    for (const id of ["D-1", "D-2"]) {
      expect(await methods(page, id)).toEqual(["keep", "mosaic"]);
      await expect(page.locator(`input[name="method-${id}"][value="mosaic"]`)).toBeChecked();
    }
    await expect(page.getByRole("radiogroup", { name: "D-1 처리 방식" }).locator("label")).toHaveText(["유지", "모자이크"]);
    await expect(page.locator(".review")).not.toContainText(/흐림|가림|대안/);
    // the ranges reach the end of the clip, and the clock shows the real duration, not the sample scene's
    await expect(page.locator(".dcard").first().locator(".range")).toContainText("00:00.0–00:04.0");
    await expect(page.locator(".player-time")).toContainText("/ 00:04.0");

    // the preview covers the face with the mosaic, and shows the bare picture the moment D-1 becomes "keep"
    await expect.poll(() => redaction(page), { timeout: 30_000 }).toBeGreaterThan(60);
    await page.locator('input[name="method-D-1"][value="keep"]').check();
    await expect.poll(() => redaction(page)).toBeLessThan(12);
    await expect(page.locator(".dcard-warning")).toBeVisible();
    await page.getByRole("button", { name: "감사 요청" }).click();

    // the audit finds the requirement the edit no longer meets
    await expect(page.getByRole("heading", { name: "감사에서 확인할 사항 1건" })).toBeVisible({ timeout: 60_000 });
    await expect(page.locator(".fcard")).toHaveCount(1);
    await expect(page.locator(".fcard-frame img")).toBeVisible();
    await page.locator(".fcard").getByText("권고 적용").click();
    await page.getByRole("button", { name: /출력 영상 만들기/ }).click();

    // result: the report's verification rows (however many it has), none failed, and the output is a real file
    await expect(page.getByRole("heading", { name: /원본과 같은 영상/ })).toBeVisible({ timeout: 120_000 });
    const verification = page.locator("section", { has: page.getByRole("heading", { name: "출력 검증" }) });
    const stored = await (await page.request.get(`/api/cases/${caseId}`)).json();
    expect(stored.report.verification.length).toBeGreaterThan(0);
    await expect(verification.locator("tbody tr")).toHaveCount(stored.report.verification.length);
    await expect(verification.locator("tbody tr", { hasText: "프레임 수" }).locator(".verdict-pass")).toHaveText("통과");
    await expect(verification.locator(".verdict-fail")).toHaveCount(0);
    await expect(verification).not.toContainText("위치 메타데이터 삭제"); // this clip has no location tag

    const link = page.getByRole("link", { name: /출력 영상/ });
    await expect(link).toHaveAttribute("href", new RegExp(`/api/cases/${caseId}/media/output\\?download=true$`));
    const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
    expect(download.suggestedFilename()).toMatch(/^clip.*\.mp4$/);
    const saved = await download.path();
    expect(fs.statSync(saved).size).toBeGreaterThan(10_000);

    const report = await page.request.get((await page.getByRole("link", { name: "보고서 열기" }).getAttribute("href")) ?? "");
    expect(report.ok()).toBe(true);
    expect(report.headers()["content-type"]).toContain("text/html");
    await expect(page.getByRole("link", { name: /보고서 JSON/ })).toBeVisible();
  });

  test("the Lab opens that run: nodes are done, the agent's memory and the checkpoints are there", async ({ page }) => {
    test.skip(!caseId, "needs the run of the first test");
    await page.goto(`/lab/runs/${caseId}`);
    await expect(page.locator(".mock-badge")).toHaveCount(0);
    await expect(page.getByText("모의 실행 속도")).toHaveCount(0);
    for (const node of ["ingest", "perceive", "legal_analyst/model", "await_review", "auditor", "render_original", "verify_output", "report"]) {
      await expect(page.locator(`.react-flow__node[data-id="${node}"] .st-done`).first()).toBeVisible({ timeout: 30_000 });
    }
    await expect(page.locator(".run-status")).toContainText("완료");
    // the bar names this run and leads back to the list; it does not choose or start runs
    await expect(page.locator(".runbar-id")).toHaveText(caseId);
    await expect(page.locator(".runbar select")).toHaveCount(0);
    await expect(page.locator(".runbar").getByRole("link", { name: "실행 목록" })).toHaveAttribute("href", "/lab/runs");
    // a finished case of the service keeps its plain link to the service screen
    await expect(page.locator(".runbar-actions").getByRole("link", { name: "서비스 화면", exact: true })).toHaveAttribute("href", `/cases/${caseId}`);

    await page.locator('.react-flow__node[data-id="legal_analyst/model"]').click();
    await expect(page.getByRole("tab", { name: "에이전트 메모리" })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(".mem-item.role-call").first()).toBeVisible();
    await expect(page.locator(".mem-item.role-tool").first()).toBeVisible();

    await page.getByRole("tab", { name: "체크포인트" }).click();
    await expect(page.locator(".ckpt").first()).toBeVisible();
    // checkpoints are read here; running on from one with other parts is a section experiment
    await expect(page.locator(".checkpoints").getByRole("button", { name: /분기|이어서 실행/ })).toHaveCount(0);
    await expect(page.locator(".checkpoints").getByRole("link", { name: "구간 실험" })).toHaveAttribute("href", "/lab/experiments");
    await expect(page.locator(".tl-bar").first()).toBeVisible();
  });
});

test("the review preview covers the face on every frame, the last one included", async ({ page }) => {
  // Regression: on a 10 s clip the preview stopped showing the redaction 0.1 s before the end, so the tail looked
  // untouched. The server now lists the mosaic of every frame and the preview draws exactly that.
  test.setTimeout(180_000);
  const id = await uploadToReview(page, clip);
  const geometry = await (await page.request.get(`/api/cases/${id}/geometry`)).json();
  const lastFrame = geometry.frames.length - 1;
  const face = geometry.decisions["D-1"];
  expect(geometry.frames[lastFrame]).toBeLessThan(geometry.durationSec);
  expect(face.ranges).toEqual([[0, geometry.durationSec]]);
  expect(face.runs.some(([first, boxes]: [number, number[][]]) => first <= lastFrame && lastFrame < first + boxes.length)).toBe(true);

  await expect.poll(() => redaction(page), { timeout: 30_000 }).toBeGreaterThan(60);
  const scrub = page.getByLabel("재생 위치");
  const shownAt = () => page.locator(".player-view video, .player-view img").evaluate((el) => (el instanceof HTMLVideoElement ? el.currentTime : Number((el as HTMLImageElement).dataset.t)));
  // inside the last frame, just before the end, and at the very end of the clip
  for (const t of [geometry.frames[lastFrame] + 0.005, geometry.durationSec - 0.01, geometry.durationSec]) {
    await scrub.fill(String(Math.round(t * 100) / 100));
    await expect.poll(shownAt).toBeGreaterThanOrEqual(geometry.frames[lastFrame] - 0.11); // a still (no video codec) is taken at tenths
    await expect.poll(() => redaction(page)).toBeGreaterThan(60);
  }
  // the measure is live at this position: keeping the face shows the bare last frame, covering it again does not
  await page.locator('input[name="method-D-1"][value="keep"]').check();
  await expect.poll(() => redaction(page)).toBeLessThan(12);
  await page.locator('input[name="method-D-1"][value="mosaic"]').check();
  await expect.poll(() => redaction(page)).toBeGreaterThan(60);

  // a range typed short of the end is still applied to the end, because the face is on screen until then: the card says so
  const card = page.locator(".dcard", { has: page.locator('input[name="method-D-1"]') });
  await card.locator(".range-input").nth(1).fill("3.7");
  await expect(card.locator(".ranges-applied")).toContainText("00:00.0–00:04.0");
  await expect.poll(() => redaction(page)).toBeGreaterThan(60);
});

test("a location tag in the file becomes a keep-or-remove decision, and removing it is verified", async ({ page }) => {
  test.setTimeout(240_000);
  const id = await uploadToReview(page, located);

  // three decisions: the face, the name tag, and the file's location tag
  await expect(page.locator(".dcard")).toHaveCount(3);
  const card = page.locator(".dcard", { hasText: "촬영 위치 정보" });
  await expect(card.locator(".tag-file")).toHaveText("파일 정보");
  await expect(card.getByRole("radiogroup").locator("label")).toHaveText(["유지", "삭제"]);
  const name = (await card.locator('input[type="radio"]').first().getAttribute("name")) ?? "";
  expect(await methods(page, name.replace("method-", ""))).toEqual(["keep", "remove"]);
  await expect(card.locator('input[value="remove"]')).toBeChecked();
  // it is information in the file: no shape, no range, no scene to jump to, and no row on the timeline
  await expect(card.locator(".ranges, select")).toHaveCount(0);
  await expect(card.getByRole("button", { name: "장면 보기" })).toHaveCount(0);
  await expect(card).toContainText("REQ-");
  await expect(page.locator(".dtl .dtl-row", { hasText: "촬영 위치 정보" })).toHaveCount(0);
  await expect(page.locator(".dtl-file-row")).toContainText(["촬영 위치 정보"]);
  // the on-screen decisions are untouched by it
  expect(await methods(page, "D-1")).toEqual(["keep", "mosaic"]);
  await expect.poll(() => redaction(page), { timeout: 30_000 }).toBeGreaterThan(60);

  // nothing was changed, so the audit has nothing to object to and the output is rendered
  await page.getByRole("button", { name: "감사 요청" }).click();
  await expect(page.getByRole("heading", { name: /원본과 같은 영상/ })).toBeVisible({ timeout: 150_000 });
  const verification = page.locator("section", { has: page.getByRole("heading", { name: "출력 검증" }) });
  const removed = verification.locator("tbody tr", { hasText: "위치 메타데이터 삭제" });
  await expect(removed.locator(".verdict-pass")).toHaveText("통과");
  await expect(removed).toContainText("남은 태그 없음");
  await expect(verification.locator(".verdict-fail")).toHaveCount(0);
  const stored = await (await page.request.get(`/api/cases/${id}`)).json();
  await expect(verification.locator("tbody tr")).toHaveCount(stored.report.verification.length);
  // the script names it for the whole file, and the notices say it is gone from the output
  const fileRow = page.locator("table.script tbody tr").last();
  await expect(fileRow.locator("td").nth(0)).toHaveText("파일 전체");
  await expect(fileRow.locator("td").nth(1)).toHaveText("촬영 위치 정보");
  await expect(fileRow.locator("td").nth(2)).toHaveText("삭제");
  await expect(page.locator(".notices")).toContainText("출력 파일에서 삭제했습니다");
});

test("Lab pages say what the real backend cannot do yet", async ({ page }) => {
  // Experiments run on the real backend now (section experiments, covered by the Lab's own real-stage spec).
  // What this file still holds the page to: on a real backend nothing in it is the mock's example data.
  await page.goto("/lab/experiments");
  await expect(page.getByRole("heading", { name: "실험", level: 1 })).toBeVisible();
  await expect(page.locator(".mock-badge")).toHaveCount(0);
  await expect(page.getByText("예시 값")).toHaveCount(0);

  // no example video on this server: a demo run cannot start, and the page says why
  // (the Lab opens on the run list, the one menu entry for runs and their topology)
  await page.goto("/lab");
  await expect(page).toHaveURL(/\/lab\/runs$/);
  await expect(page.getByRole("navigation", { name: "실험실 메뉴" }).getByRole("link")).toHaveText(["실행", "실험", "데이터셋 · 평가기", "프로필", "모델 후보"]);
  await expect(page.getByRole("button", { name: "데모 실행 시작" })).toBeDisabled();
  await expect(page.locator("#no-sample-note")).toBeVisible();
  // no column of example numbers: policy F1 is not measured by this backend
  await expect(page.getByRole("columnheader", { name: "정책 F1" })).toHaveCount(0);

  // regression experiments are not measured yet, so a profile offers neither an experiment nor a promotion
  await page.goto("/lab/profiles/baseline-v2");
  await expect(page.getByRole("heading", { name: "baseline-v2" })).toBeVisible();
  await expect(page.getByRole("link", { name: "편집" })).toBeVisible();
  await expect(page.getByRole("link", { name: "실험 시작" })).toHaveCount(0);
});
