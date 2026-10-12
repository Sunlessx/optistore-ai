import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";

// Each page is its own chunk so the first load stays small.
const page = <K extends string>(load: () => Promise<Record<K, React.ComponentType>>, name: K) => lazy(() => load().then((m) => ({ default: m[name] })));

const DashboardPage = page(() => import("./pages/DashboardPage"), "DashboardPage");
const FilesPage = page(() => import("./pages/FilesPage"), "FilesPage");
const DuplicatesPage = page(() => import("./pages/DuplicatesPage"), "DuplicatesPage");
const ComparePage = page(() => import("./pages/ComparePage"), "ComparePage");
const SearchPage = page(() => import("./pages/SearchPage"), "SearchPage");
const ConsolidationPage = page(() => import("./pages/ConsolidationPage"), "ConsolidationPage");
const CompressionPage = page(() => import("./pages/CompressionPage"), "CompressionPage");
const CostsPage = page(() => import("./pages/CostsPage"), "CostsPage");
const PredictionsPage = page(() => import("./pages/PredictionsPage"), "PredictionsPage");
const PlansPage = page(() => import("./pages/PlansPage"), "PlansPage");
const AwsPage = page(() => import("./pages/AwsPage"), "AwsPage");
const HistoryPage = page(() => import("./pages/HistoryPage"), "HistoryPage");
const SettingsPage = page(() => import("./pages/SettingsPage"), "SettingsPage");

export default function App() {
  return (
    <Layout>
      <Suspense fallback={<Spinner />}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/files" element={<FilesPage />} />
          <Route path="/duplicates" element={<DuplicatesPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/consolidation" element={<ConsolidationPage />} />
          <Route path="/compression" element={<CompressionPage />} />
          <Route path="/costs" element={<CostsPage />} />
          <Route path="/predictions" element={<PredictionsPage />} />
          <Route path="/plans" element={<PlansPage />} />
          <Route path="/aws" element={<AwsPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="text-sm text-ink-2">Page not found.</div>} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
