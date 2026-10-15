import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { Run } from "../src/types";

test.skip(
  process.env.PRIVFRAME_LIVE_E2E !== "1",
  "Use uv run python scripts/test_live.py for an isolated API/worker/Redis runtime.",
);

test("실제 worker·YuNet·ArcFace와 제품 화면에서 인물 확인·출력·재출력한다", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  test
    .info()
    .annotations.push({
      type: "scope",
      description:
        "No HTTP mocks. Real local detection, recognition, durable graph execution and rendering. Explicit developer fixture text embeddings/decision; isolated temporary storage.",
    });
  const source = readFileSync(
    new URL("../../data/demo/astronaut.mp4", import.meta.url),
  );
  const uploaded = await request.post("/api/videos", {
    multipart: {
      file: { name: "astronaut.mp4", mimeType: "video/mp4", buffer: source },
    },
  });
  expect(uploaded.ok()).toBe(true);
  const document = await request.post("/api/documents", {
    multipart: {
      file: {
        name: "synthetic.txt",
        mimeType: "text/plain",
        buffer: Buffer.from(
          "영상 열람 요청 당사자와 제3자의 개인정보를 보호하기 위한 합성 테스트 자료입니다. ".repeat(
            30,
          ),
        ),
      },
    },
  });
  expect(document.ok()).toBe(true);
  const doc = await document.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/documents/${doc.id}`)).json()).status,
    )
    .toBe("READY");
  const index = await (
    await request.post("/api/indexes", {
      data: { document_ids: [doc.id], profile: "fixture-yunet" },
    })
  ).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/indexes/${index.id}`)).json()).status,
      { timeout: 30000 },
    )
    .toBe("READY");
  const created = await request.post("/api/runs", {
    data: {
      video_id: (await uploaded.json()).id,
      index_id: index.id,
      profile: "fixture-yunet",
      scenario: {
        use_purpose: "subject_access",
        audience_scope: "designated_external",
      },
    },
  });
  expect(created.ok()).toBe(true);
  const initial = (await created.json()) as Run;
  const snapshot = async (id: string) =>
    (await (await request.get(`/api/runs/${id}`)).json()) as Run;
  await expect
    .poll(async () => (await snapshot(initial.id)).status, { timeout: 60000 })
    .toBe("AWAITING_LABELS");
  const analyzed = await snapshot(initial.id);
  expect(analyzed.identity!.groups.length).toBeGreaterThan(0);
  expect(analyzed.observation!.has_audio).toBe(true);
  const analysisExecution = await (
    await request.get(`/api/executions/${analyzed.executions!.analyze}`)
  ).json();
  expect(
    analysisExecution.nodes.map((node: { name: string }) => node.name),
  ).toEqual(["analyze_video", "group_identities"]);

  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "실험 구성" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: /작업 내역/ }).click();
  await page.getByRole("button", { name: /astronaut.mp4/ }).click();
  await page.getByLabel("인물 1 관계", { exact: true }).selectOption("self");
  await expect(page.getByLabel("인물 1 동의 여부")).toHaveCount(0);
  await page.getByLabel("인물 1 열람 요청 당사자").selectOption("yes");
  await page.getByRole("button", { name: "인물 정보 저장 후 판단" }).click();
  await expect
    .poll(async () => (await snapshot(initial.id)).status, { timeout: 30000 })
    .toBe("REVIEW_READY");
  const decided = await snapshot(initial.id);
  const facts = Object.values(decided.scenario.persons!)[0];
  expect(facts.consent).toBe("not_applicable");
  expect(facts.access_requester).toBe("yes");
  expect(decided.query_plan!.query).not.toContain(
    analyzed.identity!.groups[0].id,
  );
  expect(decided.decision!.model).toBe("fixture-only");
  await page.getByLabel("전체 처리 방법").selectOption("MASK");
  await page.getByLabel("전체 처리 강도").selectOption("2");
  await page.getByRole("button", { name: "전체 적용", exact: true }).click();
  await page
    .getByRole("button", { name: "비식별 영상 생성", exact: true })
    .click();
  await expect
    .poll(async () => (await snapshot(initial.id)).status, { timeout: 60000 })
    .toBe("COMPLETED");
  await expect(page.getByRole("link", { name: "영상 다운로드" })).toBeVisible();
  const completed = await snapshot(initial.id);
  expect(completed.render!.has_audio).toBe(true);
  const output = await request.get(
    `/api/artifacts/${completed.render!.artifact}`,
  );
  expect(output.ok()).toBe(true);
  expect((await output.body()).subarray(4, 8).toString()).toBe("ftyp");
  const html = await request.get(`/api/artifacts/${completed.reports!.html}`);
  expect(await html.text()).toContain("해당 없음");
  const revisedResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/runs/${initial.id}/revise`),
  );
  await page.getByRole("button", { name: "처리만 수정해 다시 출력" }).click();
  const revised = (await (await revisedResponse).json()) as Run;
  expect(revised.executions).toEqual({});
  await page.getByLabel("전체 처리 방법").selectOption("MOSAIC");
  await page.getByLabel("전체 처리 강도").selectOption("2");
  await page.getByRole("button", { name: "전체 적용", exact: true }).click();
  await page
    .getByRole("button", { name: "비식별 영상 생성", exact: true })
    .click();
  await expect
    .poll(async () => (await snapshot(revised.id)).status, { timeout: 60000 })
    .toBe("COMPLETED");
  const finished = await snapshot(revised.id);
  expect(finished.evidence).toEqual(completed.evidence);
  expect(finished.decision).toEqual(completed.decision);
  expect(finished.stages!.map((stage) => stage.stage)).toEqual(["render"]);
  expect(finished.render!.content_hash).not.toBe(
    completed.render!.content_hash,
  );
  expect(await snapshot(initial.id)).toEqual(completed);
});
