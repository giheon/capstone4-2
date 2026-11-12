import { useState } from "react";
import { Download, FileJson, FileText } from "lucide-react";
import type { CaseDetail } from "../../api/contract";
import { api } from "../../api/client";
import { bytes, rangeText, seconds, usd } from "../../lib/format";
import { CitationList, ErrorState, cx } from "../../ui";
import { CasePlayer, caseDuration } from "../media";
import { isOnScreen } from "../media/geometry";

export function ResultStage({ c }: { c: CaseDetail }) {
  const report = c.report;
  const duration = caseDuration(c);
  const [t, setT] = useState(() => (c.media ? Math.min(1, duration / 3) : 12));
  const [view, setView] = useState<"output" | "original">("output");
  if (!report || !c.plan) return null;
  const applied = c.findings.filter((f) => f.status === "applied").length;
  const accepted = c.findings.filter((f) => f.status === "rejected").length;
  const failed = report.verification.filter((r) => r.verdict === "fail").length;
  // a case whose output failed verification is not marked complete, but its report is still worth reading
  const unfinished = c.status === "FAILED";
  const media = c.media;
  // a script row of a decision about the file itself (the location tag) is for the whole file: it has no moment
  // to jump to, and it is listed after the rows that follow the video's time
  const inFile = new Set(c.plan.decisions.filter((d) => !isOnScreen(d)).map((d) => d.targetLabel));
  const script = [...report.script.filter((row) => !inFile.has(row.target)), ...report.script.filter((row) => inFile.has(row.target))];
  return (
    <div className="result">
      {unfinished ? (
        <ErrorState error={new Error(c.error ? `이 작업은 완료로 표시하지 않았습니다. ${c.error}` : "이 작업은 완료로 표시하지 않았습니다. 출력 검증에서 실패한 항목이 있습니다.")} />
      ) : null}
      <section className="result-top">
        <div className="result-media">
          <div className="segmented view-toggle" role="radiogroup" aria-label="보기">
            <label>
              <input type="radio" name="result-view" checked={view === "output"} onChange={() => setView("output")} />
              출력
            </label>
            <label>
              <input type="radio" name="result-view" checked={view === "original"} onChange={() => setView("original")} />
              원본
            </label>
          </div>
          <CasePlayer c={c} t={t} onTime={setT} decisions={c.plan.decisions} mode={view} caption={view === "output" ? "출력 미리보기 · 예시 장면" : "원본 · 예시 장면"} />
        </div>
        <div className="result-summary">
          <p className={cx("eyebrow", unfinished && "is-failed")}>{unfinished ? "검증 실패" : "완료"}</p>
          <h2 className="result-title">{failed || unfinished ? "출력 검증에 실패한 항목이 있습니다" : "처리한 곳 외에는 원본과 같은 영상입니다"}</h2>
          <dl className="result-stats">
            <div>
              <dt>결정</dt>
              <dd className="num">{c.plan.decisions.filter((d) => d.method !== "keep").length}건 처리 · {c.plan.decisions.filter((d) => d.method === "keep").length}건 유지</dd>
            </div>
            <div>
              <dt>감사 권고</dt>
              <dd className="num">
                적용 {applied} · 위험 수용 {accepted}
              </dd>
            </div>
            <div>
              <dt>처리 시간</dt>
              <dd className="num">{seconds(report.elapsedSec)}</dd>
            </div>
            <div>
              <dt>비용</dt>
              <dd className="num">{usd(report.costUsd)}</dd>
            </div>
          </dl>
          <div className="downloads">
            {media ? (
              media.output ? (
                <a className={cx("btn", unfinished ? "btn-secondary" : "btn-primary")} href={`${media.output}?download=true`} download={report.output.filename} aria-describedby="video-download-note">
                  <Download size={15} aria-hidden="true" />
                  출력 영상 · {bytes(report.output.sizeBytes)}
                </a>
              ) : null
            ) : (
              <button type="button" className="btn btn-primary" disabled aria-describedby="video-download-note">
                <Download size={15} aria-hidden="true" />
                출력 영상 · {bytes(report.output.sizeBytes)}
              </button>
            )}
            {media?.reportHtml ? (
              <a className="btn btn-secondary" href={media.reportHtml} target="_blank" rel="noreferrer">
                <FileText size={15} aria-hidden="true" />
                보고서 열기
              </a>
            ) : null}
            <a className="btn btn-secondary" href={api.reportUrl(c.id)} download>
              <FileJson size={15} aria-hidden="true" />
              보고서 JSON
            </a>
          </div>
          <p id="video-download-note" className="muted small">
            {media ? (
              <>
                <span className="mono">{report.output.filename}</span> · {report.output.codec}
                {unfinished ? " · 검증에 실패한 출력이므로 그대로 게시하지 마세요." : ""}
                {media.output ? "" : " · 출력 파일이 만들어지지 않았습니다."}
              </>
            ) : (
              "모의 백엔드는 영상 파일을 만들지 않습니다. 원본 스트림 렌더는 구현 단계(M4)에서 연결됩니다."
            )}
          </p>
        </div>
      </section>

      <section className="result-section" aria-labelledby="verify-title">
        <h3 id="verify-title" className="section-title">
          출력 검증
        </h3>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">항목</th>
                <th scope="col">원본</th>
                <th scope="col">출력</th>
                <th scope="col">판정</th>
              </tr>
            </thead>
            <tbody>
              {report.verification.map((row) => (
                <tr key={row.check} className={cx(row.verdict === "fail" && "is-failed")}>
                  <th scope="row" className="row-head">
                    {row.check}
                  </th>
                  <td className="mono">{row.original}</td>
                  <td className="mono">
                    {row.output}
                    {row.note ? <span className="cell-note">{row.note}</span> : null}
                  </td>
                  <td>
                    <span className={cx("verdict", `verdict-${row.verdict}`)}>{row.verdict === "pass" ? "통과" : row.verdict === "fail" ? "실패" : "참고"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {media ? (
          <p className="muted small">출력 파일을 원본과 비교해 잰 값입니다. 하나라도 실패하면 작업을 완료로 표시하지 않습니다.</p>
        ) : (
          <p className="muted small">수치는 모의 실행의 예시 값입니다. 처리 영역 밖 화질 기준(45 dB · 0.99)은 구현 단계에서 실제 영상으로 확정합니다.</p>
        )}
      </section>

      <section className="result-section" aria-labelledby="script-title">
        <h3 id="script-title" className="section-title">
          초 단위 스크립트
        </h3>
        <div className="table-wrap">
          <table className="table script">
            <thead>
              <tr>
                <th scope="col">구간</th>
                <th scope="col">장면</th>
                <th scope="col">대상</th>
                <th scope="col">처리</th>
                <th scope="col">근거</th>
              </tr>
            </thead>
            <tbody>
              {script.map((row, i) => (
                <tr key={i}>
                  {inFile.has(row.target) ? (
                    <td colSpan={2}>{row.scene || "파일 전체"}</td>
                  ) : (
                    <>
                      <td className="mono nowrap">
                        <button type="button" className="link-button" onClick={() => setT(media ? Math.min(duration, row.start + Math.min(0.5, (row.end - row.start) / 2)) : row.start + 0.5)}>
                          {rangeText([row.start, row.end])}
                        </button>
                      </td>
                      <td>{row.scene}</td>
                    </>
                  )}
                  <td>{row.target}</td>
                  <td className="nowrap">{row.treatment}</td>
                  <td className="muted">{row.basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="result-cols">
        <div className="result-col">
          <h3 className="section-title">질문 · 수정 · 감사 기록</h3>
          <ul className="history">
            {report.history.map((h, i) => (
              <li key={i}>{h}</li>
            ))}
          </ul>
        </div>
        <div className="result-col">
          <h3 className="section-title">알아 둘 점</h3>
          <ul className="notices">
            {report.notices.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
        <div className="result-col">
          <h3 className="section-title">인용한 근거</h3>
          <CitationList items={report.citations} />
        </div>
      </section>
    </div>
  );
}
