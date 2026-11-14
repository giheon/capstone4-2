import { appConfig } from "./product-fixture";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { IdentityGroup, PersonInput, Run, Track } from "../src/types";

const config = {
  name: "fixture",
  mode: "fixture",
  detector: "haar",
  llm_model: "fixture-only",
};
const video = {
  id: "video-identity",
  filename: "A_B_A.mp4",
  artifact: "videos/identity.mp4",
  content_hash: "input",
  created_at: "2026-10-09T10:00:00Z",
};
const group = (id: string, track_ids: string[]): IdentityGroup => ({
  id,
  track_ids,
  source: "automatic",
  thumbnail: null,
  review_reasons: [],
});

async function mockIdentityApi(page: Page, rejectSave = false) {
  const tracks: Track[] = [0, 20, 40].map((start, index) => ({
    id: `track-${index + 1}`,
    object_type: "face",
    thumbnail: null,
    start: start / 10,
    end: (start + 10) / 10,
    points: Array.from({ length: 10 }, (_, offset) => ({
      frame: start + offset,
      time: (start + offset) / 10,
      box: { x: 1, y: 1, width: 20, height: 20 },
      confidence: 0.95,
    })),
  }));
  let run: Run = {
    schema_version: "2",
    id: "run-identity",
    video_id: video.id,
    scenario: {
      context: "",
      use_purpose: "subject_access",
      audience_scope: "public",
      facts: {},
      track_labels: {},
      persons: {},
    },
    config,
    status: "AWAITING_LABELS",
    created_at: video.created_at,
    updated_at: video.created_at,
    observation: {
      video_id: video.id,
      original_hash: "input",
      working_artifact: video.artifact,
      working_hash: "working",
      width: 160,
      height: 120,
      fps: 10,
      frame_count: 50,
      duration: 5,
      has_audio: false,
      tracks,
      detector: "haar",
      tracker: "iou",
      analysis_fingerprint: "analysis",
      normalization: {},
    },
    identity: {
      feature_fingerprint: "features",
      grouping_fingerprint: "groups",
      automatic_groups: [
        group("p1", ["track-1", "track-3"]),
        group("p2", ["track-2"]),
      ],
      groups: [group("p1", ["track-1", "track-3"]), group("p2", ["track-2"])],
      tracks: tracks.map((track) => ({
        track_id: track.id,
        sample_frames: [track.points[0].frame],
        thumbnail: null,
        quality: 0.8,
        consistency: 0.9,
        issues: [],
      })),
      candidates: [
        {
          first_track_id: "track-1",
          second_track_id: "track-2",
          similarity: 0.43,
        },
      ],
      revision: 0,
      reviewed: false,
      user_changes: [],
      model: {},
      metrics: {},
      elapsed_seconds: 0,
    },
    decision: null,
    evidence: null,
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
  const saves: unknown[] = [];
  const splits: unknown[] = [];
  let rendered: unknown;
  let decisions = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api", "");
    let body: unknown;
    if (path === "/app-config") body = appConfig;
    else if (path === "/health")
      body = {
        status: "ok",
        api_key_configured: false,
        redis_available: true,
        ffmpeg_available: true,
        fixture_mode_available: true,
      };
    else if (path === "/profiles") body = [{ name: "fixture", config }];
    else if (path === "/videos") body = [video];
    else if (path === "/documents" || path === "/indexes") body = [];
    else if (path === "/runs") body = [run];
    else if (path === `/runs/${run.id}`) body = run;
    else if (path === `/runs/${run.id}/identity-review`) {
      const payload = request.postDataJSON();
      saves.push(payload);
      if (rejectSave || payload.revision !== run.identity!.revision) {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "인물 정보가 변경되었습니다. 새로고침 후 다시 저장하세요.",
          }),
        });
        return;
      }
      run.identity!.groups = payload.groups.map((item: IdentityGroup) => ({
        ...group(item.id, item.track_ids),
        source: "user",
      }));
      run.identity!.revision++;
      run.identity!.reviewed = true;
      run.scenario.persons = Object.fromEntries(
        run.identity!.groups.map((item) => [
          item.id,
          { ...payload.persons[item.id], track_ids: item.track_ids },
        ]),
      );
      body = run;
    } else if (path === `/runs/${run.id}/split-track`) {
      const payload = request.postDataJSON();
      splits.push(payload);
      const original = run.observation!.tracks.find(
        (item) => item.id === payload.track_id,
      )!;
      const parts = [
        original.points.filter((point) => point.frame < payload.frame),
        original.points.filter((point) => point.frame >= payload.frame),
      ];
      const children = parts.map((points, index) => ({
        ...original,
        id: `${original.id}-s-${index + 1}`,
        points,
        start: points[0].time,
        end: (points[points.length - 1].frame + 1) / 10,
      }));
      run.observation!.tracks = [
        ...run.observation!.tracks.filter((item) => item.id !== original.id),
        ...children,
      ];
      const affected = run.identity!.groups.find((item) =>
        item.track_ids.includes(original.id),
      )!;
      affected.track_ids = affected.track_ids.filter(
        (id) => id !== original.id,
      );
      run.identity!.groups = [
        ...run.identity!.groups.filter((item) => item.track_ids.length),
        ...children.map((child) => group(`p-${child.id}`, [child.id])),
      ];
      run.scenario.persons = Object.fromEntries(
        run.identity!.groups.map((item) => [
          item.id,
          item.id === affected.id || item.id.startsWith("p-track")
            ? {
                track_ids: item.track_ids,
                relationship: "unknown",
                consent: "unknown",
              }
            : run.scenario.persons![item.id],
        ]),
      ) as Record<string, PersonInput>;
      run.identity!.revision++;
      run.identity!.reviewed = false;
      body = run;
    } else if (path === `/runs/${run.id}/decide`) {
      decisions++;
      run.status = "REVIEW_REQUIRED";
      run.review_reasons = ["처리 방법을 확인하세요."];
      run.decision = {
        model: "fixture-only",
        plan: {
          decisions: run.observation!.tracks.map((track) => ({
            track_id: track.id,
            treatment: { action: "BLUR", strength: 2 },
            reason: "검토용 제안",
            evidence_ids: [],
          })),
          summary: "인물별 처리를 확인하세요.",
          needs_review: true,
          missing_facts: [],
        },
      };
      body = run;
    } else if (path === `/runs/${run.id}/overrides`) {
      rendered = request.postDataJSON();
      run.overrides = request.postDataJSON().treatments;
      body = run;
    } else if (path === `/runs/${run.id}/render`) {
      run.status = "COMPLETED";
      run.effective_treatments = run.overrides;
      run.manual_output = true;
      run.render = {
        artifact: "runs/identity/result.mp4",
        content_hash: "output",
        frame_count: 50,
        has_audio: false,
        parameters: {},
        treatments: run.overrides!,
      };
      body = run;
    } else if (path.startsWith("/artifacts/")) {
      await route.fulfill({ status: 200, contentType: "video/mp4", body: "" });
      return;
    } else {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: `Unexpected route ${path}` }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  return {
    snapshot: () => structuredClone(run),
    saves,
    splits,
    decisions: () => decisions,
    rendered: () => rendered,
  };
}

async function openExisting(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /A_B_A.mp4/ }).click();
}

test("인물 입력은 동의를 추정하지 않고 저장되며 병합·분리 후에는 초기화된다", async ({
  page,
}) => {
  const api = await mockIdentityApi(page);
  await openExisting(page);
  await expect(page.getByLabel("인물 1 동의 여부")).toHaveValue("unknown");
  await page.getByLabel("인물 1 관계", { exact: true }).selectOption("self");
  await expect(page.getByLabel("인물 1 동의 여부")).toHaveCount(0);
  await expect(
    page.getByText("동의 여부: 해당 없음", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("인물 1 열람 요청 당사자").selectOption("yes");
  await page
    .getByRole("button", { name: "인물 정보 저장", exact: true })
    .click();
  await expect(page.getByText("인물 정보를 저장했습니다.")).toBeVisible();
  expect(api.snapshot().scenario.persons!.p1).toEqual({
    track_ids: ["track-1", "track-3"],
    relationship: "self",
    consent: "not_applicable",
    access_requester: "yes",
  });
  await page.reload();
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /A_B_A.mp4/ }).click();
  await expect(page.getByLabel("인물 1 동의 여부")).toHaveCount(0);
  await expect(page.getByLabel("인물 1 열람 요청 당사자")).toHaveValue("yes");
  await page.getByLabel("인물 2 합칠 대상").selectOption("p1");
  await page
    .getByRole("article", { name: "인물 2", exact: true })
    .getByRole("button", { name: "합치기", exact: true })
    .click();
  await expect(page.getByLabel("인물 1 관계", { exact: true })).toHaveValue(
    "unknown",
  );
  await expect(page.getByLabel("인물 1 동의 여부")).toHaveValue("unknown");
  await expect(
    page.getByRole("article", { name: "인물 2", exact: true }),
  ).toHaveCount(0);
  await page.getByText("등장 구간 확인 · 3개 트랙", { exact: true }).click();
  await page.getByRole("button", { name: "track-2 별도 인물로 분리" }).click();
  await expect(page.getByLabel("인물 2 동의 여부")).toHaveValue("unknown");
  await page
    .getByRole("button", { name: "인물 정보 저장", exact: true })
    .click();
  await expect(page.getByText("인물 정보를 저장했습니다.")).toBeVisible();
  const snapshot = api.snapshot();
  expect(snapshot.identity!.groups.map((item) => item.track_ids)).toEqual([
    ["track-1", "track-3"],
    ["track-2"],
  ]);
  expect(
    Object.values(snapshot.scenario.persons!).every(
      (person) => person.consent === "unknown",
    ),
  ).toBe(true);
  await page.reload();
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /A_B_A.mp4/ }).click();
  await expect(page.getByRole("article", { name: /^인물 [12]$/ })).toHaveCount(
    2,
  );
});

test("프레임 분할은 수정한 인물 정보를 먼저 저장하고 새 구간을 불러온다", async ({
  page,
}) => {
  const api = await mockIdentityApi(page);
  await openExisting(page);
  await page.getByLabel("인물 2 동의 여부").selectOption("refused");
  await page.getByText("등장 구간 확인 · 2개 트랙", { exact: true }).click();
  const track = page.locator(".identity-track").filter({ hasText: "track-1" });
  await track.getByText("트랙 구간 나누기", { exact: true }).click();
  await page.getByLabel("track-1 분할 프레임", { exact: true }).fill("5");
  await track.getByRole("button", { name: "이 프레임에서 분할" }).click();
  await expect(page.getByRole("article", { name: /^인물 [1-4]$/ })).toHaveCount(
    4,
  );
  expect(api.splits).toEqual([{ revision: 1, track_id: "track-1", frame: 5 }]);
  expect(api.snapshot().scenario.persons!.p2.consent).toBe("refused");
  expect(
    api
      .snapshot()
      .observation!.tracks.filter((item) => item.id.startsWith("track-1-s"))
      .flatMap((item) => item.points)
      .map((point) => point.frame),
  ).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  await page.reload();
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /A_B_A.mp4/ }).click();
  await expect(page.getByText("track-1-s-1", { exact: true })).toBeVisible();
  await expect(page.getByText("track-1-s-2", { exact: true })).toBeVisible();
});

test("인물별 처리는 모든 소속 트랙에 적용되고 개별 처리는 다른 인물에 영향을 주지 않는다", async ({
  page,
}) => {
  const api = await mockIdentityApi(page);
  await openExisting(page);
  await page.getByLabel("인물 1 동의 여부").selectOption("granted");
  await page.getByLabel("인물 2 동의 여부").selectOption("refused");
  await page.getByRole("button", { name: "인물 정보 저장 후 판단" }).click();
  await expect(
    page.getByRole("heading", { name: "인물별 처리 검토" }),
  ).toBeVisible();
  expect(api.decisions()).toBe(1);
  await expect(page.getByLabel("인물 1 관계", { exact: true })).toHaveCount(0);
  const output = page.getByRole("button", {
    name: "사용자 지정으로 영상 생성",
  });
  await expect(output).toBeDisabled();
  await page
    .getByLabel("인물 1 처리 방법", { exact: true })
    .selectOption("KEEP");
  await page.getByRole("button", { name: "인물 1 전체 적용" }).click();
  await expect(output).toBeDisabled();
  await page.getByRole("button", { name: "인물 2 전체 적용" }).click();
  await expect(output).toBeEnabled();
  await page
    .getByText("인물 1 등장 구간별 처리 · 2개 트랙", { exact: true })
    .click();
  await page
    .getByLabel("track-3 처리 방법", { exact: true })
    .selectOption("MOSAIC");
  await output.click();
  await expect(page.getByRole("link", { name: "영상 다운로드" })).toBeVisible();
  expect(api.rendered()).toEqual({
    treatments: {
      "track-1": { action: "KEEP", strength: null },
      "track-3": { action: "MOSAIC", strength: 2 },
      "track-2": { action: "MASK", strength: 3 },
    },
    no_faces_confirmed: false,
  });
});

test("저장 충돌은 판단 요청을 중단하고 오류를 표시한다", async ({ page }) => {
  const api = await mockIdentityApi(page, true);
  await openExisting(page);
  await page.getByRole("button", { name: "인물 정보 저장 후 판단" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "새로고침 후 다시 저장하세요",
  );
  expect(api.decisions()).toBe(0);
});

test("모바일 인물 검토 화면은 프레임 편집을 펼쳐도 가로로 잘리지 않는다", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockIdentityApi(page);
  await openExisting(page);
  await page.getByText("트랙 구간 나누기", { exact: true }).last().click();
  await expect(
    page.getByRole("button", { name: "인물 정보 저장 후 판단" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("identity-mobile.png"),
    fullPage: true,
  });
});
