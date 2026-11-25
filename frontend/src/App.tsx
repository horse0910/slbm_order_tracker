import {lazy,Suspense} from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { LiveAlertsProvider } from "@/context/LiveAlertsProvider";
const DashboardPage=lazy(()=>import("@/pages/DashboardPage").then(m=>({default:m.DashboardPage})));
import { PortfolioPage } from "@/pages/PortfolioPage";
import { LiveSLBPage } from "@/pages/LiveSLBPage";
import { LiveBoardPage } from "@/pages/LiveBoardPage";
const SLBQuotePage=lazy(()=>import("@/pages/SLBQuotePage").then(m=>({default:m.SLBQuotePage})));
import { ContractsPage } from "@/pages/ContractsPage";
const RolloverPage=lazy(()=>import("@/pages/RolloverPage").then(m=>({default:m.RolloverPage})));
import { AlertsPage } from "@/pages/AlertsPage";
const PnLPage=lazy(()=>import("@/pages/PnLPage").then(m=>({default:m.PnLPage})));
const AnalyticsPage=lazy(()=>import("@/pages/AnalyticsPage").then(m=>({default:m.AnalyticsPage})));
import { SettingsPage } from "@/pages/SettingsPage";
import { PlaceholderPage } from "@/pages/PlaceholderPage";
import { FUTURE_NAV } from "@/utils/navigation";

export default function App() {
  return (
    <LiveAlertsProvider>
      <AppShell>
        <Suspense fallback={<div className="p-8 text-slate-500">Loading page…</div>}><Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/live" element={<LiveBoardPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/slb" element={<LiveSLBPage />} />
          <Route path="/slb/:symbol" element={<SLBQuotePage />} />
          <Route path="/contracts" element={<ContractsPage />} />
          <Route path="/rollover" element={<RolloverPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
          <Route path="/pnl" element={<PnLPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          {FUTURE_NAV.filter((item) => !item.implemented).map((item) => (
            <Route
              key={item.path}
              path={item.path}
              element={<PlaceholderPage title={item.label} phase={item.phase} />}
            />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes></Suspense>
      </AppShell>
    </LiveAlertsProvider>
  );
}
