import { Link, NavLink, Outlet, useLocation } from "react-router";
import { Plus } from "lucide-react";
import { useBackendKind } from "../api/hooks";
import { BrandMark, MockBadge } from "../ui";

export function CategoryNav() {
  const { pathname } = useLocation();
  const inLab = pathname.startsWith("/lab");
  return (
    <nav className="category-nav" aria-label="영역">
      <NavLink to="/" className={inLab ? "" : "active"} aria-current={inLab ? undefined : "page"}>
        서비스
      </NavLink>
      <NavLink to="/lab" className={inLab ? "active" : ""} aria-current={inLab ? "page" : undefined}>
        실험실
      </NavLink>
    </nav>
  );
}

export function ServiceLayout() {
  const { pathname } = useLocation();
  const mock = useBackendKind() === "mock";
  return (
    <div className="app-root theme-service">
      <header className="app-header">
        <Link to="/" className="brand" aria-label="PrivFrame 홈">
          <BrandMark />
          PrivFrame
        </Link>
        <CategoryNav />
        <div className="header-right">
          {mock ? <MockBadge /> : null}
          {pathname !== "/" ? (
            <Link to="/" className="btn btn-sm btn-secondary">
              <Plus size={15} aria-hidden="true" />새 작업
            </Link>
          ) : null}
        </div>
      </header>
      <Outlet />
    </div>
  );
}
