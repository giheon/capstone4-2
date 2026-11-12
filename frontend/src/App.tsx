import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import { ServiceLayout } from "./shell/ServiceLayout";
import { HomePage } from "./service/HomePage";
import { CasePage } from "./service/CasePage";
import { NotFound } from "./shell/NotFound";
import { Spinner } from "./ui";

// the Lab (React Flow, experiment tables) loads on demand so the service screens stay light
const LabLayout = lazy(() => import("./lab/LabLayout").then((m) => ({ default: m.LabLayout })));
const TopologyPage = lazy(() => import("./lab/TopologyPage").then((m) => ({ default: m.TopologyPage })));
const RunsPage = lazy(() => import("./lab/RunsPage").then((m) => ({ default: m.RunsPage })));
const ExperimentsPage = lazy(() => import("./lab/ExperimentsPage").then((m) => ({ default: m.ExperimentsPage })));
const StageExperimentBuilder = lazy(() => import("./lab/StageExperimentBuilder").then((m) => ({ default: m.StageExperimentBuilder })));
const PipelineExperimentsPage = lazy(() => import("./lab/PipelineExperiments").then((m) => ({ default: m.PipelineExperimentsPage })));
const DatasetsPage = lazy(() => import("./lab/DatasetsPage").then((m) => ({ default: m.DatasetsPage })));
const ProfilesPage = lazy(() => import("./lab/ProfilesPage").then((m) => ({ default: m.ProfilesPage })));
const ProfileEditor = lazy(() => import("./lab/ProfileEditor").then((m) => ({ default: m.ProfileEditor })));
const CatalogPage = lazy(() => import("./lab/CatalogPage").then((m) => ({ default: m.CatalogPage })));

function LabFallback() {
  return (
    <div className="app-root theme-lab lab-loading">
      <Spinner size={18} label="실험실 불러오는 중" />
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<LabFallback />}>
      <Routes>
        <Route element={<ServiceLayout />}>
          <Route index element={<HomePage />} />
          <Route path="cases/:caseId" element={<CasePage />} />
        </Route>
        <Route path="lab" element={<LabLayout />}>
          {/* the Lab opens on the run list; a run's topology is entered from there */}
          <Route index element={<Navigate replace to="/lab/runs" />} />
          <Route path="runs" element={<RunsPage />} />
          <Route path="runs/:runId" element={<TopologyPage />} />
          {/* section experiments; the whole-pipeline evaluation experiments of the later milestone are under `pipeline` */}
          <Route path="experiments" element={<ExperimentsPage />} />
          <Route path="experiments/new" element={<StageExperimentBuilder />} />
          <Route path="experiments/pipeline" element={<PipelineExperimentsPage />} />
          <Route path="experiments/pipeline/:experimentId" element={<PipelineExperimentsPage />} />
          <Route path="experiments/:experimentId" element={<ExperimentsPage />} />
          <Route path="datasets" element={<DatasetsPage />} />
          <Route path="profiles" element={<ProfilesPage />} />
          <Route path="profiles/new" element={<ProfileEditor />} />
          <Route path="profiles/:profileId" element={<ProfilesPage />} />
          <Route path="profiles/:profileId/edit" element={<ProfileEditor />} />
          <Route path="catalog" element={<CatalogPage />} />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
