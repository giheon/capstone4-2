import { appConfig } from "./product-fixture";
import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

const config = {
  name: "fixture",
  mode: "fixture",
  detector: "haar",
  chunk_size: 1200,
  llm_model: "fixture-only",
};
const video = {
  id: "video-1",
  filename: "sample.mp4",
  content_hash: "video-hash",
  artifact: "videos/video-1/original.mp4",
  created_at: "2026-10-02T10:00:00Z",
};
const evidence = {
  id: "chunk-1",
  document_id: "doc-1",
  text: "모델에게 전달된 실제 근거 문구",
  page: 3,
  article: "제35조",
  offset: 0,
  metadata: {
    title: "테스트 법령 자료",
    source_url: "https://example.com/source",
    kind: "fixture",
    version: "테스트 버전",
  },
  content_hash: "chunk-hash",
  score: 0.9,
  index_id: "index-1",
};

async function mockApi(page: Page, noFaces = false) {
  const tracks = noFaces
    ? []
    : ["track-1", "track-2"].map((id) => ({
        id,
        points: [
          {
            frame: 0,
            time: 0,
            box: { x: 1, y: 1, width: 20, height: 20 },
            confidence: 0.95,
          },
        ],
        thumbnail: null,
        start: 0,
        end: 1,
      }));
  let run: Record<string, unknown> = {
    schema_version: "1",
    id: "run-1",
    video_id: video.id,
    index_id: "index-1",
    parent_id: null,
    scenario: {
      context: "공동주택의 영상 열람 요청을 검토합니다.",
      facts: {},
      track_labels: {},
    },
    config,
    created_at: "2026-10-02T10:00:00Z",
    updated_at: "2026-10-02T10:00:00Z",
    status: "AWAITING_LABELS",
    observation: {
      video_id: video.id,
      original_hash: "original",
      working_artifact: video.artifact,
      working_hash: "working",
      width: 160,
      height: 120,
      fps: 30,
      frame_count: 30,
      duration: 1,
      has_audio: false,
      tracks,
      detector: "haar",
      tracker: "iou",
      analysis_fingerprint: "analysis",
      normalization: {},
    },
    evidence: {
      query: "열람 요청",
      index_id: "index-1",
      candidates: [
        evidence,
        {
          ...evidence,
          id: "chunk-2",
          text: "모델에게 전달되지 않은 검색 후보",
        },
      ],
      provided: [evidence],
      retrieved_at: "2026-10-02T10:00:00Z",
    },
    decision: null,
    review_reasons: [],
    overrides: {},
    effective_treatments: {},
    manual_output: false,
    no_faces_confirmed: false,
    stages: [],
    render: null,
    reports: {},
    error: null,
  };
  let overridePayload: Record<string, unknown> | undefined;
  let renderCount = 0;
  let decisionCount = 0;
  const parents = new Map<string, Record<string, unknown>>();
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api", "");
    let body: unknown = null;
    let responseStatus = 200;
    if (path === "/app-config") body = appConfig;
    else if (path === "/health")
      body = {
        status: "ok",
        api_key_configured: false,
        redis_available: true,
        ffmpeg_available: true,
        fixture_mode_available: true,
      };
    else if (path === "/profiles")
      body = [
        {
          name: "baseline",
          config: { ...config, name: "baseline", mode: "real" },
        },
        { name: "fixture", config },
      ];
    else if (path === "/videos")
      body = request.method() === "POST" ? video : [video];
    else if (path === "/documents" || path === "/indexes") body = [];
    else if (path === "/runs" && request.method() === "GET")
      body = [run, ...parents.values()];
    else if (path === "/runs" && request.method() === "POST") {
      run = { ...run, scenario: request.postDataJSON().scenario };
      body = run;
    } else if (/^\/runs\/[^/]+$/.test(path) && request.method() === "GET")
      body = path === `/runs/${run.id}` ? run : parents.get(path.split("/")[2]);
    else if (path === `/runs/${run.id}/labels`) {
      run = {
        ...run,
        scenario: {
          ...(run.scenario as object),
          track_labels: request.postDataJSON().track_labels,
        },
      };
      body = run;
    } else if (path === `/runs/${run.id}/decide`) {
      decisionCount++;
      run = {
        ...run,
        status: "REVIEW_REQUIRED",
        review_reasons: ["추가 동의 여부를 확인해야 합니다."],
        decision: {
          plan: {
            decisions: tracks.map((track) => ({
              track_id: track.id,
              treatment: { action: "BLUR", strength: 2 },
              reason: "예시 모델 제안",
              evidence_ids: ["chunk-1"],
            })),
            summary: "추가 정보가 필요합니다.",
            needs_review: true,
            missing_facts: ["동의 여부"],
          },
          raw: {},
          prompt: [],
          model: "fixture-only",
          usage: {},
          error: null,
        },
      };
      body = run;
    } else if (path === `/runs/${run.id}/overrides`) {
      overridePayload = request.postDataJSON();
      run = {
        ...run,
        overrides: overridePayload!.treatments,
        no_faces_confirmed: overridePayload!.no_faces_confirmed,
      };
      body = run;
    } else if (path === `/runs/${run.id}/render`) {
      renderCount++;
      run = {
        ...run,
        status: "COMPLETED",
        manual_output: true,
        effective_treatments: run.overrides,
        stages: [
          ...(run.stages as object[]),
          { id: `render-${renderCount}`, stage: "render", status: "COMPLETED" },
        ],
        render: {
          artifact: `runs/${run.id}/result.mp4`,
          content_hash: `output-${run.id}`,
          treatments: run.overrides,
          parameters: {},
          frame_count: 30,
          has_audio: false,
        },
        reports: {
          html: `runs/${run.id}/report.html`,
          json: `runs/${run.id}/report.json`,
        },
      };
      body = run;
    } else if (path === `/runs/${run.id}/revise`) {
      const original = structuredClone(run);
      parents.set(String(original.id), original);
      run = {
        ...structuredClone(original),
        id: "run-2",
        parent_id: original.id,
        reused_stages: { analyze: original.id, decide: original.id },
        status: "REVIEW_REQUIRED",
        stages: [],
        render: null,
        reports: {},
        effective_treatments: {},
        manual_output: false,
      };
      responseStatus = 201;
      body = run;
    } else if (path.startsWith("/artifacts/")) {
      await route.fulfill({ status: 200, contentType: "video/mp4", body: "" });
      return;
    } else {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unexpected test API path: ${path}` }),
      });
      return;
    }
    await route.fulfill({
      status: responseStatus,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return {
    overrides: () => overridePayload,
    rendered: () => renderCount,
    decided: () => decisionCount,
    snapshot: (id: string) =>
      structuredClone(id === run.id ? run : parents.get(id)),
  };
}

async function openExisting(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /sample.mp4/ }).click();
  await expect(
    page.getByRole("button", { name: "이 설정으로 판정 생성" }),
  ).toBeVisible();
}

test("검토 필요 실행은 모든 얼굴을 명시적으로 지정해야 출력할 수 있다", async ({
  page,
}) => {
  const api = await mockApi(page);
  await openExisting(page);
  await expect(page.getByRole("combobox", { name: "실험 구성" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  const output = page.getByRole("button", {
    name: "사용자 지정으로 영상 생성",
  });
  await expect(output).toBeDisabled();
  await page.getByLabel("track-1 처리 방법").selectOption("KEEP");
  await expect(output).toBeDisabled();
  expect(api.rendered()).toBe(0);
  await page.getByLabel("track-2 처리 방법").selectOption("MASK");
  await expect(output).toBeEnabled();
  await output.click();
  await expect(page.getByRole("link", { name: "영상 다운로드" })).toBeVisible();
  expect(api.overrides()).toEqual({
    treatments: {
      "track-1": { action: "KEEP", strength: null },
      "track-2": { action: "MASK", strength: 2 },
    },
    no_faces_confirmed: false,
  });
  expect(api.rendered()).toBe(1);
  await expect(
    page.getByText("추가 동의 여부를 확인해야 합니다."),
  ).toBeVisible();
});

test("전체 지정과 검색 후보, 실제 입력, 인용 근거를 구분한다", async ({
  page,
}) => {
  const api = await mockApi(page);
  await openExisting(page);
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  await page.getByText("테스트 법령 자료", { exact: true }).click();
  await expect(
    page.getByText("모델에게 전달된 실제 근거 문구", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("모델에게 전달되지 않은 검색 후보", { exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".evidence-card")).toHaveCount(1);
  await expect(page.getByRole("tab")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: test.info().outputPath("review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "전체 적용" }).click();
  await page.getByRole("button", { name: "사용자 지정으로 영상 생성" }).click();
  await expect(page.getByRole("link", { name: "보고서 보기" })).toBeVisible();
  expect(api.overrides()).toEqual({
    treatments: {
      "track-1": { action: "MASK", strength: 3 },
      "track-2": { action: "MASK", strength: 3 },
    },
    no_faces_confirmed: false,
  });
});

test("얼굴 0개에서는 빈 처리 맵만으로 원본 출력을 허용하지 않는다", async ({
  page,
}) => {
  const api = await mockApi(page, true);
  await openExisting(page);
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  await expect(
    page.getByText("얼굴이 검출되지 않았습니다.", { exact: true }),
  ).toBeVisible();
  const output = page.getByRole("button", {
    name: "사용자 지정으로 영상 생성",
  });
  await expect(output).toBeDisabled();
  expect(api.rendered()).toBe(0);
  await page.getByRole("checkbox", { name: /검출 결과를 확인했고/ }).check();
  await output.click();
  await expect(page.getByRole("link", { name: "영상 다운로드" })).toBeVisible();
  expect(api.overrides()).toEqual({ treatments: {}, no_faces_confirmed: true });
});

test("완료 출력에서 처리만 수정하면 이전 기록을 보존하고 새 영상만 생성한다", async ({
  page,
}) => {
  const api = await mockApi(page);
  await openExisting(page);
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  await page.getByRole("button", { name: "전체 적용" }).click();
  await page.getByRole("button", { name: "사용자 지정으로 영상 생성" }).click();
  const revise = page.getByRole("button", { name: "처리만 수정해 다시 출력" });
  await expect(revise).toBeVisible();
  const original = api.snapshot("run-1");
  await revise.click();

  await expect(
    page.getByText("이전 분석과 판단을 불러왔습니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "영상 다운로드" })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("track-1 처리 방법")).toHaveValue("MASK");
  await expect(page.getByLabel("track-2 처리 방법")).toHaveValue("MASK");
  expect(api.snapshot("run-2")).toMatchObject({
    parent_id: "run-1",
    status: "REVIEW_REQUIRED",
    reused_stages: { analyze: "run-1", decide: "run-1" },
    stages: [],
    render: null,
    reports: {},
  });
  await page.getByLabel("track-1 처리 방법").selectOption("MOSAIC");
  await page.getByLabel("track-1 처리 강도").selectOption("2");
  await page.getByRole("button", { name: "사용자 지정으로 영상 생성" }).click();
  await expect(
    page.getByRole("link", { name: "영상 다운로드" }),
  ).toHaveAttribute("href", "/api/artifacts/runs/run-2/result.mp4");
  expect(api.overrides()).toEqual({
    treatments: {
      "track-1": { action: "MOSAIC", strength: 2 },
      "track-2": { action: "MASK", strength: 3 },
    },
    no_faces_confirmed: false,
  });
  expect(api.rendered()).toBe(2);
  expect(api.decided()).toBe(1);
  expect(api.snapshot("run-1")).toEqual(original);
  expect(api.snapshot("run-2")).toMatchObject({
    stages: [{ stage: "render", status: "COMPLETED" }],
  });
});

test("두 선택지만으로 작업을 생성하고 실험·문서 관리 화면을 노출하지 않는다", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("combobox")).toHaveCount(2);
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "근거 자료" })).toHaveCount(0);
  const start = page.getByRole("button", { name: "영상 분석 시작" });
  await expect(start).toBeDisabled();
  await page
    .locator("#video-file")
    .setInputFiles({
      name: "new.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("test"),
    });
  await page
    .getByLabel("이 영상을 어떤 목적으로 사용하려고 하나요?")
    .selectOption("social_publish");
  await page.getByLabel("이 영상을 누가 볼 수 있나요?").selectOption("public");
  const pending = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/runs") && request.method() === "POST",
  );
  await start.click();
  const sent = (await pending).postDataJSON();
  expect(sent.profile).toBeUndefined();
  expect(sent.index_id).toBeUndefined();
  expect(sent.scenario).toEqual({
    context: "",
    use_purpose: "social_publish",
    audience_scope: "public",
  });
});

test("서비스 미준비 상태에서는 처리를 시작하지 않는다", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/app-config", (route) =>
    route.fulfill({
      json: {
        ...appConfig,
        ready: false,
        messages: ["판단에 사용할 근거 자료를 준비하고 있습니다."],
      },
    }),
  );
  await page.goto("/");
  await expect(page.getByText("서비스 준비 중", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "영상 분석 시작" }),
  ).toBeDisabled();
});

test("모바일 화면에서도 입력과 검토가 가로로 잘리지 않는다", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "영상 분석 시작" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /sample.mp4/ }).click();
  await page.getByRole("button", { name: "이 설정으로 판정 생성" }).click();
  await expect(page.getByRole("button", { name: "전체 적용" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
