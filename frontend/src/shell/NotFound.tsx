import { Link } from "react-router";

export function NotFound() {
  return (
    <div className="app-root theme-service not-found">
      <div className="not-found-body">
        <p className="eyebrow">404</p>
        <h1>없는 페이지입니다</h1>
        <p className="muted">주소를 확인하거나 처음 화면으로 돌아가세요.</p>
        <div className="not-found-actions">
          <Link to="/" className="btn btn-primary">
            서비스로
          </Link>
          <Link to="/lab" className="btn btn-secondary">
            실험실로
          </Link>
        </div>
      </div>
    </div>
  );
}
