import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Image, ScanFace, Scissors, Users } from "lucide-react";
import { artifactUrl, post } from "./api";
import { Empty, ErrorNotice, Panel, seconds } from "./components";
import { useInputOptions } from "./InputContext";
import { consentNames, labelNames } from "./types";
import type { IdentityGroup, PersonInput, Run, Track } from "./types";

type Facts = Pick<PersonInput, "relationship" | "consent" | "access_requester">;
const unknownFacts = (): Facts => ({
  relationship: "unknown",
  consent: "unknown",
  access_requester: "unknown",
});
const issueNames: Record<string, string> = {
  missing_landmarks:
    "얼굴의 눈·코 위치를 확인하기 어려워 자동 연결하지 않았습니다.",
  low_quality: "얼굴이 작거나 흐려 같은 사람인지 직접 확인해야 합니다.",
  mixed_identity:
    "트랙 안에서 얼굴 특징이 달라집니다. 다른 사람이 이어졌는지 확인하고 필요하면 구간을 나누세요.",
  insufficient_samples: "비교할 얼굴 장면이 부족해 자동 연결하지 않았습니다.",
  manual_split_requires_review:
    "나눈 구간의 인물과 동의 여부를 다시 확인하세요.",
};

function personFacts(run: Run) {
  return Object.fromEntries(
    (run.identity?.groups ?? []).map((group) => [
      group.id,
      run.scenario.persons?.[group.id] ?? unknownFacts(),
    ]),
  );
}

export function FaceThumbnail({
  artifact,
  label,
}: {
  artifact?: string | null;
  label: string;
}) {
  const [failedArtifact, setFailedArtifact] = useState<string | null>(null);
  return artifact && artifact !== failedArtifact ? (
    <img
      src={artifactUrl(artifact)}
      alt={label}
      loading="lazy"
      onError={() => setFailedArtifact(artifact)}
    />
  ) : (
    <span className="thumbnail-placeholder">
      <Image size={23} />
      썸네일 없음
    </span>
  );
}

export default function IdentityReview({
  run,
  update,
}: {
  run: Run;
  update: (run: Run) => void;
}) {
  const options = useInputOptions();
  const [groups, setGroups] = useState<IdentityGroup[]>(
    run.identity!.groups ?? [],
  );
  const [persons, setPersons] = useState<Record<string, Facts>>(() =>
    personFacts(run),
  );
  const [revision, setRevision] = useState(run.identity!.revision);
  const [changedIds, setChangedIds] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const tracks = run.observation!.tracks;
  const editable = run.status === "AWAITING_LABELS";

  const adopt = (result: Run) => {
    setGroups(result.identity!.groups ?? []);
    setPersons(personFacts(result));
    setRevision(result.identity!.revision);
    setChangedIds([]);
    setDirty(false);
    setSaved(true);
    update(result);
  };

  useEffect(() => {
    // Refreshes from another tab must not silently overwrite an unsaved draft.
    // Its old revision will cause the server to reject a stale save.
    if (!dirty && run.identity!.revision > revision) {
      setGroups(run.identity!.groups ?? []);
      setPersons(personFacts(run));
      setRevision(run.identity!.revision);
    }
  }, [run, revision, dirty]);

  const persist = async () => {
    const result = await post<Run>(`/runs/${run.id}/identity-review`, {
      revision,
      groups: groups.map(({ id, track_ids }) => ({ id, track_ids })),
      persons: Object.fromEntries(
        groups.map((group) => [
          group.id,
          {
            relationship: persons[group.id]?.relationship ?? "unknown",
            consent: persons[group.id]?.consent ?? "unknown",
            access_requester:
              run.scenario.use_purpose === "subject_access"
                ? (persons[group.id]?.access_requester ?? "unknown")
                : "unknown",
          },
        ]),
      ),
      confirmed_group_ids: groups.map((group) => group.id),
    });
    adopt(result);
    return result;
  };
  const save = useMutation({ mutationFn: persist });
  const decide = useMutation({
    mutationFn: async () => {
      await persist();
      return post<Run>(`/runs/${run.id}/decide`);
    },
    onSuccess: update,
  });
  const split = useMutation({
    mutationFn: async ({ track, frame }: { track: Track; frame: number }) => {
      const current = await persist();
      return post<Run>(`/runs/${run.id}/split-track`, {
        revision: current.identity!.revision,
        track_id: track.id,
        frame,
      });
    },
    onSuccess: adopt,
  });
  const pending = save.isPending || decide.isPending || split.isPending;
  const touch = () => {
    setDirty(true);
    setSaved(false);
    save.reset();
    decide.reset();
    split.reset();
  };
  const resetPeople = (ids: string[]) => {
    setPersons((current) => ({
      ...current,
      ...Object.fromEntries(ids.map((id) => [id, unknownFacts()])),
    }));
    setChangedIds((current) => [...new Set([...current, ...ids])]);
    touch();
  };
  const merge = (source: string, destination: string) => {
    const from = groups.find((group) => group.id === source)!;
    setGroups((current) =>
      current
        .filter((group) => group.id !== source)
        .map((group) =>
          group.id === destination
            ? {
                ...group,
                track_ids: [...group.track_ids, ...from.track_ids],
                source: "user",
              }
            : group,
        ),
    );
    resetPeople([destination]);
  };
  const separate = (groupId: string, trackId: string) => {
    const id = `person_${crypto.randomUUID().replaceAll("-", "")}`;
    const track = tracks.find((item) => item.id === trackId)!;
    setGroups((current) => [
      ...current.map((group) => {
        if (group.id !== groupId) return group;
        const remaining = group.track_ids.filter((item) => item !== trackId);
        return {
          ...group,
          track_ids: remaining,
          thumbnail: tracks.find(
            (item) => remaining.includes(item.id) && item.thumbnail,
          )?.thumbnail,
          source: "user" as const,
          review_reasons: [],
        };
      }),
      {
        id,
        track_ids: [trackId],
        source: "user",
        thumbnail: track.thumbnail,
        review_reasons: [],
      },
    ]);
    resetPeople([groupId, id]);
  };
  const seek = (time: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.pause();
    }
  };
  const groupName = (group: IdentityGroup) =>
    `인물 ${groups.indexOf(group) + 1}`;
  const trackGroup = (id: string) =>
    groups.find((group) => group.track_ids.includes(id));
  const candidates = (run.identity!.candidates ?? []).filter(
    (candidate) =>
      trackGroup(candidate.first_track_id)?.id !==
      trackGroup(candidate.second_track_id)?.id,
  );

  return (
    <Panel
      title="인물별 관계와 동의를 확인하세요"
      subtitle={`${groups.length}명으로 묶인 ${tracks.length}개 얼굴 트랙 · 같은 인물인지 확인한 뒤 정보를 입력하세요.`}
      accessory={<Users size={20} className="muted" />}
    >
      <p className="identity-intro">
        다른 사람의 동의 여부를 확인해 주세요. 모르는 정보는 ‘미확인’으로
        전달됩니다.
      </p>
      <video
        ref={videoRef}
        controls
        preload="metadata"
        className="result-video identity-video"
        src={artifactUrl(run.observation!.working_artifact)}
        aria-label="인물 확인 영상"
      />
      {groups.length === 0 && (
        <Empty>
          <ScanFace size={29} />
          <strong>얼굴이 검출되지 않았습니다.</strong>
        </Empty>
      )}
      <div className="identity-grid">
        {groups.map((group, index) => {
          const name = groupName(group);
          const groupTracks = group.track_ids.flatMap(
            (id) => tracks.find((track) => track.id === id) ?? [],
          );
          const representative =
            group.thumbnail ??
            groupTracks.find((track) => track.thumbnail)?.thumbnail;
          return (
            <article className="identity-card" key={group.id} aria-label={name}>
              <div className="identity-heading">
                <div className="identity-portrait">
                  <FaceThumbnail
                    artifact={representative}
                    label={`${name} 대표 얼굴`}
                  />
                </div>
                <div>
                  <h3>{name}</h3>
                  <span>{group.track_ids.length}개 등장 트랙</span>
                </div>
                {group.source === "user" && (
                  <span className="override-tag">사용자 수정</span>
                )}
              </div>
              {(group.review_reasons ?? []).map((reason) => (
                <p className="identity-warning" key={reason}>
                  {issueNames[reason] ?? reason}
                </p>
              ))}
              {changedIds.includes(group.id) && (
                <p className="identity-warning" role="status">
                  구성이 변경되어 관계와 동의를 미확인으로 초기화했습니다. 인물
                  정보를 다시 확인하세요.
                </p>
              )}
              <div className="identity-facts">
                <label className="field compact">
                  관계
                  <select
                    aria-label={`${name} 관계`}
                    disabled={!editable || pending}
                    value={persons[group.id]?.relationship ?? "unknown"}
                    onChange={(event) => {
                      setPersons((current) => ({
                        ...current,
                        [group.id]: {
                          ...current[group.id],
                          relationship: event.target
                            .value as Facts["relationship"],
                          consent:
                            event.target.value === "self"
                              ? "not_applicable"
                              : current[group.id]?.relationship === "self"
                                ? "unknown"
                                : (current[group.id]?.consent ?? "unknown"),
                        },
                      }));
                      touch();
                    }}
                  >
                    {(
                      options?.relationship ??
                      Object.entries(labelNames).map(([value, label]) => ({
                        value,
                        label,
                      }))
                    ).map(({ value: key, label: value }) => (
                      <option key={key} value={key}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
                {persons[group.id]?.relationship === "self" ? (
                  <p className="identity-person-facts">동의 여부: 해당 없음</p>
                ) : (
                  <label className="field compact">
                    이번 이용·공개에 대한 동의
                    <select
                      aria-label={`${name} 동의 여부`}
                      disabled={!editable || pending}
                      value={persons[group.id]?.consent ?? "unknown"}
                      onChange={(event) => {
                        setPersons((current) => ({
                          ...current,
                          [group.id]: {
                            ...current[group.id],
                            consent: event.target.value as Facts["consent"],
                          },
                        }));
                        touch();
                      }}
                    >
                      {(
                        options?.consent ??
                        Object.entries(consentNames).map(([value, label]) => ({
                          value,
                          label,
                        }))
                      )
                        .filter((option) => option.value !== "not_applicable")
                        .map(({ value: key, label: value }) => (
                          <option key={key} value={key}>
                            {value}
                          </option>
                        ))}
                    </select>
                  </label>
                )}
                {run.scenario.use_purpose === "subject_access" && (
                  <label className="field compact">
                    영상 열람을 요청한 당사자인가요?
                    <select
                      aria-label={`${name} 열람 요청 당사자`}
                      disabled={!editable || pending}
                      value={persons[group.id]?.access_requester ?? "unknown"}
                      onChange={(event) => {
                        setPersons((current) => ({
                          ...current,
                          [group.id]: {
                            ...current[group.id],
                            access_requester: event.target
                              .value as Facts["access_requester"],
                          },
                        }));
                        touch();
                      }}
                    >
                      <option value="unknown">알 수 없음</option>
                      <option value="yes">열람 요청 당사자</option>
                      <option value="no">다른 사람</option>
                    </select>
                  </label>
                )}
              </div>
              <details
                className="identity-tracks"
                open={groupTracks.length === 1}
              >
                <summary>등장 구간 확인 · {groupTracks.length}개 트랙</summary>
                {groupTracks.map((track) => {
                  const summary = run.identity!.tracks?.find(
                    (item) => item.track_id === track.id,
                  );
                  return (
                    <div className="identity-track" key={track.id}>
                      <div className="identity-track-summary">
                        <div className="identity-track-thumb">
                          <FaceThumbnail
                            artifact={summary?.thumbnail ?? track.thumbnail}
                            label={`${track.id} 얼굴 썸네일`}
                          />
                        </div>
                        <div>
                          <strong>{track.id}</strong>
                          <button
                            className="text-button"
                            onClick={() => seek(track.start)}
                          >
                            {seconds(track.start)} — {seconds(track.end)} 구간
                            보기
                          </button>
                        </div>
                      </div>
                      {(summary?.issues ?? []).map((issue) => (
                        <p className="identity-warning" key={issue}>
                          {issueNames[issue] ?? issue}
                        </p>
                      ))}
                      {editable && (
                        <div className="identity-track-actions">
                          {group.track_ids.length > 1 && (
                            <button
                              className="button secondary small"
                              disabled={pending}
                              onClick={() => separate(group.id, track.id)}
                              aria-label={`${track.id} 별도 인물로 분리`}
                            >
                              별도 인물로 분리
                            </button>
                          )}
                          {track.points.length > 1 && (
                            <TrackSplit
                              track={track}
                              fps={run.observation!.fps}
                              busy={pending}
                              seek={seek}
                              currentFrame={() =>
                                Math.round(
                                  (videoRef.current?.currentTime ??
                                    track.start) * run.observation!.fps,
                                )
                              }
                              split={(frame) => split.mutate({ track, frame })}
                            />
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </details>
              {editable && groups.length > 1 && (
                <MergeControl
                  group={group}
                  name={name}
                  others={groups.filter((item) => item.id !== group.id)}
                  names={groups.map((item, number) => ({
                    id: item.id,
                    name: `인물 ${number + 1}`,
                  }))}
                  disabled={pending}
                  merge={merge}
                />
              )}
              <span className="sr-only">{index + 1}번째 인물</span>
            </article>
          );
        })}
      </div>
      {candidates.length > 0 && (
        <details className="details-block">
          <summary>같은 인물일 수 있는 연결 · {candidates.length}개</summary>
          <p>
            유사도는 얼굴 특징의 비교 값입니다. 동일인 확률을 뜻하지 않습니다.
          </p>
          <ul className="identity-candidates">
            {candidates.map((candidate) => {
              const first = trackGroup(candidate.first_track_id);
              const second = trackGroup(candidate.second_track_id);
              return (
                <li
                  key={`${candidate.first_track_id}:${candidate.second_track_id}`}
                >
                  {first ? groupName(first) : candidate.first_track_id} ↔{" "}
                  {second ? groupName(second) : candidate.second_track_id} ·
                  유사도 {candidate.similarity.toFixed(3)}
                  <small>
                    {candidate.first_track_id} / {candidate.second_track_id}
                  </small>
                </li>
              );
            })}
          </ul>
        </details>
      )}
      <ErrorNotice error={save.error ?? decide.error ?? split.error} />
      {saved && !dirty && (
        <p className="identity-saved" role="status">
          인물 정보를 저장했습니다.
        </p>
      )}
      {editable && (
        <div className="panel-action">
          <p>저장하면 현재 인물 구성과 입력 내용을 확인한 것으로 기록합니다.</p>
          <div className="identity-save-actions">
            <button
              className="button secondary"
              disabled={pending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "저장 중…" : "인물 정보 저장"}
            </button>
            <button
              className="button primary"
              disabled={pending}
              onClick={() => decide.mutate()}
            >
              {decide.isPending ? "판정 요청 중…" : "인물 정보 저장 후 판단"}
              <ArrowRight size={17} />
            </button>
          </div>
        </div>
      )}
    </Panel>
  );
}

function MergeControl({
  group,
  name,
  others,
  names,
  disabled,
  merge,
}: {
  group: IdentityGroup;
  name: string;
  others: IdentityGroup[];
  names: { id: string; name: string }[];
  disabled: boolean;
  merge: (source: string, destination: string) => void;
}) {
  const [destination, setDestination] = useState("");
  return (
    <div className="identity-merge">
      <label className="field compact">
        같은 인물로 합치기
        <select
          aria-label={`${name} 합칠 대상`}
          disabled={disabled}
          value={destination}
          onChange={(event) => setDestination(event.target.value)}
        >
          <option value="">인물 선택</option>
          {others.map((other) => (
            <option value={other.id} key={other.id}>
              {names.find((item) => item.id === other.id)!.name}
            </option>
          ))}
        </select>
      </label>
      <button
        className="button secondary small"
        disabled={disabled || !others.some((other) => other.id === destination)}
        onClick={() => merge(group.id, destination)}
      >
        합치기
      </button>
    </div>
  );
}

function TrackSplit({
  track,
  fps,
  busy,
  seek,
  currentFrame,
  split,
}: {
  track: Track;
  fps: number;
  busy: boolean;
  seek: (time: number) => void;
  currentFrame: () => number;
  split: (frame: number) => void;
}) {
  const points = track.points;
  const minimum = points[0].frame + 1;
  const maximum = points[points.length - 1].frame;
  const [frame, setFrame] = useState(
    points[Math.floor(points.length / 2)].frame,
  );
  const valid = Number.isInteger(frame) && frame >= minimum && frame <= maximum;
  const setPosition = (value: number) => {
    setFrame(value);
    if (Number.isFinite(value) && value >= minimum && value <= maximum)
      seek(value / fps);
  };
  return (
    <details className="identity-split">
      <summary>
        <Scissors size={13} />
        트랙 구간 나누기
      </summary>
      <p>
        한 트랙에 다른 사람이 이어졌다면 두 번째 사람의 시작 프레임을
        선택하세요. 현재 인물 정보도 함께 저장합니다.
      </p>
      <label className="field compact">
        분할 위치
        <input
          type="range"
          aria-label={`${track.id} 분할 타임라인`}
          min={minimum}
          max={maximum}
          step="1"
          value={frame}
          disabled={busy}
          onChange={(event) => setPosition(Number(event.target.value))}
        />
      </label>
      <div className="identity-split-controls">
        <label className="field compact">
          프레임
          <input
            type="number"
            aria-label={`${track.id} 분할 프레임`}
            min={minimum}
            max={maximum}
            value={Number.isNaN(frame) ? "" : frame}
            disabled={busy}
            onChange={(event) => setPosition(event.target.valueAsNumber)}
          />
        </label>
        <button
          className="button secondary small"
          disabled={busy}
          onClick={() =>
            setPosition(Math.min(maximum, Math.max(minimum, currentFrame())))
          }
        >
          영상의 현재 위치
        </button>
        <button
          className="button secondary small"
          disabled={busy || !valid}
          onClick={() => split(frame)}
        >
          {busy ? "저장 중…" : "이 프레임에서 분할"}
        </button>
      </div>
    </details>
  );
}
