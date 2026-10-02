import { test, expect } from "@playwright/test";
import type { APIRequestContext } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import type { DocumentRecord, IndexRecord, Run } from "../src/types";

test.skip(
  process.env.PRIVFRAME_LIVE_E2E !== "1",
  "Set PRIVFRAME_LIVE_E2E=1 with the real API, Redis, worker and astronaut demo ready.",
);

async function snapshot(request: APIRequestContext, id: string): Promise<Run> {
  const response = await request.get(`/api/runs/${id}`);
  expect(response.ok(), await response.text()).toBe(true);
  return response.json() as Promise<Run>;
}

test("실제 API·worker·YuNet으로 출력한다 (임베딩·판정은 명시적 fixture)", async ({
  page,
  request,
}) => {
  test.setTimeout(240_000);
  test.info().annotations.push({
    type: "scope",
    description:
      "No HTTP mocks. Actual YuNet, tracking, MP4 rendering and official stored sources; fixture embeddings and fixture decision only.",
  });

  const clip = fileURLToPath(
    new URL("../../data/demo/astronaut.mp4", import.meta.url),
  );
  expect(existsSync(clip), "Prepare data/demo/astronaut.mp4 first").toBe(true);

  const healthResponse = await request.get("/api/health");
  expect(healthResponse.ok(), await healthResponse.text()).toBe(true);
  const health = await healthResponse.json();
  expect(health.redis_available, "Redis must be running").toBe(true);
  expect(health.ffmpeg_available, "FFmpeg/FFprobe must be available").toBe(
    true,
  );
  expect(
    health.yunet_available,
    "The verified YuNet asset must be available",
  ).toBe(true);

  const profilesResponse = await request.get("/api/profiles");
  expect(profilesResponse.ok()).toBe(true);
  const profiles = (await profilesResponse.json()) as {
    name: string;
    config: Record<string, unknown>;
  }[];
  const profile = profiles.find((item) => item.name === "fixture-yunet");
  expect(
    profile,
    "Prepare the explicit fixture-yunet profile first",
  ).toBeTruthy();
  expect(profile!.config.mode).toBe("fixture");
  expect(profile!.config.detector).toBe("yunet");
  expect(profile!.config.decider).toBe("fixture");

  const documentsResponse = await request.get("/api/documents");
  expect(documentsResponse.ok()).toBe(true);
  const documents = (await documentsResponse.json()) as DocumentRecord[];
  const official = documents.filter(
    (document) =>
      document.status === "READY" &&
      ["law", "guide"].includes(document.metadata.kind ?? "") &&
      /^https:\/\/www\.(law|privacy)\.go\.kr\//.test(
        document.metadata.source_url ?? "",
      ),
  );
  const selected = [
    ...new Map(
      official.map((document) => [document.metadata.source_url, document]),
    ).values(),
  ];
  expect(
    selected.length,
    "Seed and extract the three official sources first",
  ).toBeGreaterThanOrEqual(3);
  const documentIds = selected.map((document) => document.id);
  const indexResponse = await request.post("/api/indexes", {
    data: { document_ids: documentIds, profile: "fixture-yunet" },
  });
  expect(indexResponse.ok(), await indexResponse.text()).toBe(true);
  const index = (await indexResponse.json()) as IndexRecord;
  await expect
    .poll(
      async () => {
        const response = await request.get(`/api/indexes/${index.id}`);
        expect(response.ok(), await response.text()).toBe(true);
        const current = (await response.json()) as IndexRecord;
        if (current.status === "FAILED") {
          throw new Error(`Live indexing failed: ${current.error}`);
        }
        return current.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("READY");

  await page.goto("/");
  const profileSelect = page.getByRole("combobox", {
    name: "실험 구성",
    exact: true,
  });
  await profileSelect.selectOption("fixture-yunet");
  await expect(
    page.getByText("테스트 픽스처 · 실제 모델 결과가 아닙니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "검색할 자료 묶음" })
    .selectOption(index.id);
  await page.locator("#video-file").setInputFiles(clip);
  const context =
    "공개된 우주비행사 사진으로 만든 테스트 영상입니다. 행사 기록을 참가자들에게 공유하려는 상황을 가정하며, 얼굴별 처리 효과를 비교합니다.";
  await page
    .getByRole("textbox", { name: /촬영 상황과 이용 목적/ })
    .fill(context);
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/runs") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "영상 분석 시작" }).click();
  const creation = await createdResponse;
  expect(creation.ok(), await creation.text()).toBe(true);
  const created = (await creation.json()) as Run;

  await expect
    .poll(
      async () => {
        const current = await snapshot(request, created.id);
        if (current.status === "FAILED")
          throw new Error(`Live analysis failed: ${current.error}`);
        return current.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("AWAITING_LABELS");
  const analyzed = await snapshot(request, created.id);
  const tracks = analyzed.observation?.tracks ?? [];
  expect(
    tracks.length,
    "Actual YuNet must detect the astronaut face",
  ).toBeGreaterThan(0);
  expect(analyzed.observation!.detector).toBe("yunet");
  expect(analyzed.observation!.has_audio).toBe(true);
  expect(analyzed.observation!.normalization?.detector_model_hash).toMatch(
    /^[a-f0-9]{64}$/,
  );

  await expect(
    page.getByRole("button", { name: "이 설정으로 판정 생성" }),
  ).toBeVisible({ timeout: 10_000 });
  const target = tracks[0].id;
  await expect(page.getByLabel(`${target} 관계`)).toHaveValue("unknown");
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  await expect
    .poll(
      async () => {
        const current = await snapshot(request, created.id);
        if (current.status === "FAILED")
          throw new Error(`Live decision failed: ${current.error}`);
        return ["REVIEW_READY", "REVIEW_REQUIRED"].includes(
          current.status ?? "",
        );
      },
      { timeout: 30_000, intervals: [500, 1000, 2000] },
    )
    .toBe(true);
  const decided = await snapshot(request, created.id);
  expect(decided.decision?.model).toBe("fixture-only");
  expect(decided.config.decider).toBe("fixture");
  expect(decided.evidence?.provided?.length).toBeGreaterThan(0);
  expect(
    decided.evidence!.provided!.every((item) =>
      documentIds.includes(item.document_id),
    ),
  ).toBe(true);
  const savedEvidence = decided.evidence;
  const prompt = JSON.parse(
    decided.decision!.prompt!.find((item) => item.role === "user")!.content,
  ) as { provided_evidence: { id: string; text: string }[] };
  expect(
    prompt.provided_evidence.map(({ id, text }) => ({ id, text })),
  ).toEqual(savedEvidence!.provided!.map(({ id, text }) => ({ id, text })));

  if (decided.status === "REVIEW_REQUIRED") {
    await page
      .getByRole("combobox", { name: "전체 처리 방법" })
      .selectOption("MASK");
    await page
      .getByRole("combobox", { name: "전체 처리 강도" })
      .selectOption("2");
    await page.getByRole("button", { name: "전체 적용" }).click();
  } else {
    await page.getByLabel(`${target} 처리 방법`).selectOption("MASK");
    await page.getByLabel(`${target} 처리 강도`).selectOption("2");
  }
  const output = page.getByRole("button", {
    name: /^(비식별 영상 생성|사용자 지정으로 실험 출력)$/,
  });
  await expect(output).toBeEnabled();
  await output.click();
  await expect
    .poll(
      async () => {
        const current = await snapshot(request, created.id);
        if (current.status === "FAILED")
          throw new Error(`Live rendering failed: ${current.error}`);
        return current.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("COMPLETED");
  const finished = await snapshot(request, created.id);
  expect(finished.evidence).toEqual(savedEvidence);
  expect(finished.render?.treatments[target]).toEqual({
    action: "MASK",
    strength: 2,
  });
  expect(finished.render?.frame_count).toBeGreaterThan(0);
  expect(finished.render?.has_audio).toBe(true);

  const videoLink = page.getByRole("link", { name: "영상 다운로드" });
  await expect(videoLink).toBeVisible({ timeout: 10_000 });
  const outputResponse = await request.get(
    (await videoLink.getAttribute("href"))!,
  );
  expect(outputResponse.ok()).toBe(true);
  expect(outputResponse.headers()["content-type"]).toMatch(/video\/mp4/);
  const videoBytes = await outputResponse.body();
  expect(videoBytes.length).toBeGreaterThan(1024);
  expect(videoBytes.subarray(4, 8).toString("ascii")).toBe("ftyp");
  expect(createHash("sha256").update(videoBytes).digest("hex")).toBe(
    finished.render!.content_hash,
  );
  const downloading = page.waitForEvent("download");
  await videoLink.click();
  const download = await downloading;
  expect(await download.failure()).toBeNull();
  await download.saveAs(test.info().outputPath("live-output.mp4"));

  const htmlResponse = await request.get(
    (await page
      .getByRole("link", { name: "보고서 보기", exact: true })
      .getAttribute("href"))!,
  );
  expect(htmlResponse.ok()).toBe(true);
  const html = await htmlResponse.text();
  expect(html).toContain("테스트용 fixture 실행입니다");
  expect(html).toContain(savedEvidence!.provided![0].id);
  const reportResponse = await request.get(
    (await page
      .getByRole("link", { name: "실행 기록 JSON", exact: true })
      .getAttribute("href"))!,
  );
  expect(reportResponse.ok()).toBe(true);
  const report = (await reportResponse.json()) as Run;
  expect(report.config.mode).toBe("fixture");
  expect(report.observation!.detector).toBe("yunet");
  expect(report.observation!.normalization?.detector_model_hash).toBe(
    finished.observation!.normalization?.detector_model_hash,
  );
  expect(report.decision?.model).toBe("fixture-only");
  expect(report.evidence).toEqual(savedEvidence);
  expect(report.render?.treatments[target]).toEqual({
    action: "MASK",
    strength: 2,
  });
  expect(report.scenario.context).toBe(context);

  await page
    .getByRole("button", { name: "저장된 기록으로 보고서 다시 만들기" })
    .click();
  await expect(
    page.getByRole("button", { name: "저장된 기록으로 보고서 다시 만들기" }),
  ).toBeEnabled();
  const reloaded = await snapshot(request, created.id);
  expect(reloaded.evidence).toEqual(savedEvidence);
  expect(reloaded.decision?.prompt).toEqual(decided.decision?.prompt);

  const originalHtmlUrl = (await page
    .getByRole("link", { name: "보고서 보기", exact: true })
    .getAttribute("href"))!;
  const originalJsonUrl = (await page
    .getByRole("link", { name: "실행 기록 JSON", exact: true })
    .getAttribute("href"))!;
  const originalVideoUrl = (await videoLink.getAttribute("href"))!;
  const preservedHtmlResponse = await request.get(originalHtmlUrl);
  const preservedJsonResponse = await request.get(originalJsonUrl);
  expect(preservedHtmlResponse.ok()).toBe(true);
  expect(preservedJsonResponse.ok()).toBe(true);
  const preservedHtml = await preservedHtmlResponse.body();
  const preservedJson = await preservedJsonResponse.body();
  let repeatedDecisions = 0;
  page.on("request", (sent) => {
    if (sent.method() === "POST" && /\/decide$/.test(sent.url()))
      repeatedDecisions++;
  });
  const revisedResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/api/runs/${created.id}/revise`) &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "처리만 수정해 다시 출력" }).click();
  const revision = await revisedResponse;
  expect(revision.status(), await revision.text()).toBe(201);
  const revised = (await revision.json()) as Run;
  expect(revised.id).not.toBe(created.id);
  expect(revised.parent_id).toBe(created.id);
  expect(revised.reused_stages).toEqual({
    analyze: created.id,
    decide: created.id,
  });
  expect(["REVIEW_READY", "REVIEW_REQUIRED"]).toContain(revised.status);
  expect(revised.stages).toEqual([]);
  expect(revised.render).toBeNull();
  expect(revised.reports).toEqual({});
  expect(revised.scenario).toEqual(reloaded.scenario);
  expect(revised.observation).toEqual(reloaded.observation);
  expect(revised.evidence).toEqual(savedEvidence);
  expect(revised.decision).toEqual(reloaded.decision);
  expect(revised.overrides).toEqual(reloaded.overrides);
  expect(await snapshot(request, created.id)).toEqual(reloaded);
  await expect(
    page.getByText("이전 실행의 분석·검색 근거·판정을 재사용합니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByLabel(`${target} 처리 방법`)).toHaveValue("MASK");
  await expect(
    page.getByRole("button", { name: "이 설정으로 판정 생성" }),
  ).toHaveCount(0);
  if (revised.status === "REVIEW_REQUIRED") {
    await page
      .getByRole("combobox", { name: "전체 처리 방법" })
      .selectOption("MOSAIC");
    await page
      .getByRole("combobox", { name: "전체 처리 강도" })
      .selectOption("2");
    await page.getByRole("button", { name: "전체 적용" }).click();
  } else {
    await page.getByLabel(`${target} 처리 방법`).selectOption("MOSAIC");
    await page.getByLabel(`${target} 처리 강도`).selectOption("2");
  }
  await expect(output).toBeEnabled();
  await output.click();
  await expect
    .poll(
      async () => {
        const current = await snapshot(request, revised.id);
        if (current.status === "FAILED")
          throw new Error(`Live revised rendering failed: ${current.error}`);
        return current.status;
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBe("COMPLETED");
  const revisedFinished = await snapshot(request, revised.id);
  expect(revisedFinished.evidence).toEqual(savedEvidence);
  expect(revisedFinished.observation).toEqual(reloaded.observation);
  expect(revisedFinished.decision).toEqual(reloaded.decision);
  expect(revisedFinished.stages!.map((stage) => stage.stage)).toEqual([
    "render",
  ]);
  expect(revisedFinished.render?.treatments[target]).toEqual({
    action: "MOSAIC",
    strength: 2,
  });
  expect(revisedFinished.render?.has_audio).toBe(true);
  expect(repeatedDecisions).toBe(0);
  await expect(videoLink).toHaveAttribute(
    "href",
    `/api/artifacts/${revisedFinished.render!.artifact}`,
    { timeout: 10_000 },
  );
  const revisedOutputResponse = await request.get(
    (await videoLink.getAttribute("href"))!,
  );
  expect(revisedOutputResponse.ok()).toBe(true);
  expect(revisedOutputResponse.headers()["content-type"]).toMatch(/video\/mp4/);
  const revisedVideoBytes = await revisedOutputResponse.body();
  expect(revisedVideoBytes.length).toBeGreaterThan(1024);
  expect(revisedVideoBytes.subarray(4, 8).toString("ascii")).toBe("ftyp");
  const revisedHash = createHash("sha256")
    .update(revisedVideoBytes)
    .digest("hex");
  expect(revisedHash).toBe(revisedFinished.render!.content_hash);
  expect(revisedHash).not.toBe(finished.render!.content_hash);
  const revisedDownloading = page.waitForEvent("download");
  await videoLink.click();
  const revisedDownload = await revisedDownloading;
  expect(await revisedDownload.failure()).toBeNull();
  await revisedDownload.saveAs(
    test.info().outputPath("live-revised-output.mp4"),
  );

  const revisedHtmlResponse = await request.get(
    (await page
      .getByRole("link", { name: "보고서 보기", exact: true })
      .getAttribute("href"))!,
  );
  expect(revisedHtmlResponse.ok()).toBe(true);
  const revisedHtml = await revisedHtmlResponse.text();
  expect(revisedHtml).toContain(revised.id);
  expect(revisedHtml).toContain("테스트용 fixture 실행입니다");
  expect(revisedHtml).toContain(savedEvidence!.provided![0].id);
  const revisedReportResponse = await request.get(
    (await page
      .getByRole("link", { name: "실행 기록 JSON", exact: true })
      .getAttribute("href"))!,
  );
  expect(revisedReportResponse.ok()).toBe(true);
  const revisedReport = (await revisedReportResponse.json()) as Run;
  expect(revisedReport.parent_id).toBe(created.id);
  expect(revisedReport.reused_stages).toEqual(revised.reused_stages);
  expect(revisedReport.config.mode).toBe("fixture");
  expect(revisedReport.observation).toEqual(reloaded.observation);
  expect(revisedReport.decision).toEqual(reloaded.decision);
  expect(revisedReport.evidence).toEqual(savedEvidence);
  expect(revisedReport.render?.treatments[target]).toEqual({
    action: "MOSAIC",
    strength: 2,
  });
  expect(await snapshot(request, created.id)).toEqual(reloaded);
  const preservedVideoAfter = await request.get(originalVideoUrl);
  const preservedHtmlAfter = await request.get(originalHtmlUrl);
  const preservedJsonAfter = await request.get(originalJsonUrl);
  expect(preservedVideoAfter.ok()).toBe(true);
  expect(preservedHtmlAfter.ok()).toBe(true);
  expect(preservedJsonAfter.ok()).toBe(true);
  expect(await preservedVideoAfter.body()).toEqual(videoBytes);
  expect(await preservedHtmlAfter.body()).toEqual(preservedHtml);
  expect(await preservedJsonAfter.body()).toEqual(preservedJson);

  await page.evaluate(() => window.scrollTo(0, 0));
  const qaDirectory = fileURLToPath(new URL("../../data/qa/", import.meta.url));
  mkdirSync(qaDirectory, { recursive: true });
  const screenshot = join(qaDirectory, `live-workflow-${created.id}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  writeFileSync(
    join(qaDirectory, `live-workflow-${created.id}.json`),
    JSON.stringify(
      {
        run_id: created.id,
        revised_run_id: revised.id,
        mode: finished.config.mode,
        detector: finished.observation!.detector,
        detector_model_hash:
          finished.observation!.normalization?.detector_model_hash,
        decision_model: finished.decision!.model,
        track_count: tracks.length,
        provided_evidence_count: savedEvidence!.provided!.length,
        render: finished.render,
        reports: finished.reports,
        revised_render: revisedFinished.render,
        revised_reports: revisedFinished.reports,
        revised_stages: revisedFinished.stages,
        reused_stages: revisedFinished.reused_stages,
        parent_snapshot_and_artifacts_unchanged: true,
        scope:
          "Live API, Redis, worker, actual YuNet and rendering; fixture embeddings and decisions; no OpenAI validation",
      },
      null,
      2,
    ),
  );
  await test
    .info()
    .attach("live-workflow", { path: screenshot, contentType: "image/png" });
  await test
    .info()
    .attach("live-run-id", { body: created.id, contentType: "text/plain" });
});
