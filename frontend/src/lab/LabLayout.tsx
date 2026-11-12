import { Link, NavLink, Outlet } from "react-router";
import { useBackendKind, useSetSpeed, useSettings } from "../api/hooks";
import { CategoryNav } from "../shell/ServiceLayout";
import { BrandMark, MockBadge } from "../ui";

const SPEEDS = [0.5, 1, 2, 4, 6, 8];

export function LabLayout() {
  // playback speed and the mock badge belong to the M2 mock; real runs take the time they take
  const mock = useBackendKind() === "mock";
  return (
    <div className="app-root theme-lab lab">
      <header className="app-header lab-header">
        <Link to="/lab/runs" className="brand" aria-label="PrivFrame 실험실">
          <BrandMark />
          PrivFrame
        </Link>
        <CategoryNav />
        <nav className="lab-nav" aria-label="실험실 메뉴">
          {/* one run at a time: the list, and the topology of the run opened from it (`/lab/runs/:id`) */}
          <NavLink to="/lab/runs">실행</NavLink>
          {/* many runs: put together, accumulated and compared */}
          <NavLink to="/lab/experiments">실험</NavLink>
          <NavLink to="/lab/datasets">데이터셋 · 평가기</NavLink>
          <NavLink to="/lab/profiles">프로필</NavLink>
          <NavLink to="/lab/catalog">모델 후보</NavLink>
        </nav>
        {mock ? (
          <div className="header-right">
            <SpeedControl />
            <MockBadge title="M2 모의 백엔드: 실행 이벤트는 실제 LangGraph 스트림과 같은 형식으로 흉내 낸 것입니다." />
          </div>
        ) : null}
      </header>
      <Outlet />
    </div>
  );
}

function SpeedControl() {
  const settings = useSettings();
  const setSpeed = useSetSpeed();
  const value = settings.data?.speed ?? 1;
  return (
    <label className="speed">
      <span>모의 실행 속도</span>
      <select className="select" value={value} onChange={(e) => setSpeed.mutate(Number(e.target.value))} disabled={setSpeed.isPending}>
        {(SPEEDS.includes(value) ? SPEEDS : [...SPEEDS, value].sort((a, b) => a - b)).map((s) => (
          <option key={s} value={s}>
            {s}×
          </option>
        ))}
      </select>
    </label>
  );
}
