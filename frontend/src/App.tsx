import { QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ThemeProvider } from "./components/ThemeProvider";
import { ToastProvider } from "./components/ToastProvider";
import { Skeleton } from "./components/ui";
import { queryClient } from "./lib/query";

const OverviewPage = lazy(() => import("./pages/OverviewPage"));
const JobsPage = lazy(() => import("./pages/JobsPage"));
const ShortlistPage = lazy(() => import("./pages/ShortlistPage"));
const InternshipsPage = lazy(() => import("./pages/InternshipsPage"));
const ApplicationsPage = lazy(() => import("./pages/ApplicationsPage"));
const JobDetailPage = lazy(() => import("./pages/JobDetailPage"));
const OperationsPage = lazy(() => import("./pages/OperationsPage"));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage"));
const ExportsPage = lazy(() => import("./pages/ExportsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"));

function PageFallback() {
  return <div className="page-stack" aria-label="Loading page"><Skeleton className="skeleton--title" /><Skeleton className="skeleton--panel" /></div>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <BrowserRouter>
            <Suspense fallback={<PageFallback />}>
              <Routes>
                <Route element={<AppShell />}>
                  <Route index element={<OverviewPage />} />
                  <Route path="jobs" element={<JobsPage />} />
                  <Route path="jobs/:id" element={<JobDetailPage />} />
                  <Route path="shortlist" element={<ShortlistPage />} />
                  <Route path="internships" element={<InternshipsPage />} />
                  <Route path="applications" element={<ApplicationsPage />} />
                  <Route path="operations" element={<OperationsPage />} />
                  <Route path="analytics" element={<AnalyticsPage />} />
                  <Route path="exports" element={<ExportsPage />} />
                  <Route path="settings" element={<SettingsPage />} />
                  <Route path="*" element={<NotFoundPage />} />
                </Route>
              </Routes>
            </Suspense>
          </BrowserRouter>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
