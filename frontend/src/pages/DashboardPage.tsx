import { useEffect } from "react";
import { dashboardApi } from "@/api/dashboard";
import { useApiResource } from "@/hooks/useApiResource";
import { useLiveAlerts } from "@/context/LiveAlertsProvider";
import { DASHBOARD_POLL_INTERVAL_MS } from "@/utils/config";
import { Card, SectionHeader } from "@/components/Card";
import { StatCard } from "@/components/StatCard";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import { FreshnessIndicator } from "@/components/Freshness";
import { DaysBadge } from "@/components/StatusPill";
import { OpportunityYieldChart } from "@/components/OpportunityYieldChart";
import { CopyTicketButton } from "@/components/CopyTicketButton";
import {
  formatCompactINR,
  formatDate,
  formatDateTime,
  formatINR,
  formatPercent,
  formatPrice,
  formatQty,
} from "@/utils/format";
import type { DashboardSummary } from "@/types";

export function DashboardPage() {
  const { data, error, loading, refreshing, lastUpdated, refetch } =
    useApiResource<DashboardSummary>((signal) => dashboardApi.summary(signal), [], {
      pollIntervalMs: DASHBOARD_POLL_INTERVAL_MS,
    });

  // Real-time push (Phase 4): a fired-alert broadcast refreshes the summary
  // (recent-alerts panel + counts) immediately instead of waiting for the poll.
  const { eventCount } = useLiveAlerts();
  useEffect(() => {
    if (eventCount > 0) refetch();
  }, [eventCount, refetch]);

  if (error && !data) {
    return (
      <Card className="panel-pad">
        <ErrorState error={error} onRetry={refetch} />
      </Card>
    );
  }

  const portfolio = data?.portfolio;
  const contracts = data?.contracts;
  const income = data?.income;
  const lentOut = data?.lent_out;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Portfolio & lending overview using live backend data.
        </p>
        <div className="flex items-center gap-3">
          <FreshnessIndicator
            timestamp={data?.generated_at}
            lastUpdated={lastUpdated}
            refreshing={refreshing}
          />
          <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
            Refresh
          </button>
        </div>
      </div>

      {/* Portfolio overview */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Tracked stocks"
          value={formatQty(portfolio?.total_stocks ?? 0)}
          hint={`${formatQty(portfolio?.total_owned ?? 0)} shares held`}
          subhint={`in demat ${formatQty(portfolio?.in_demat ?? 0)} · lent ${formatQty(portfolio?.lent ?? 0)}`}
          loading={loading}
        />
        <StatCard
          label="SLB-eligible stocks"
          value={formatQty(portfolio?.slb_eligible_stocks ?? 0)}
          hint="Available to lend"
          loading={loading}
          accent="pos"
        />
        <StatCard
          label="Active contracts"
          value={formatQty(contracts?.active ?? 0)}
          hint={`${formatQty(contracts?.expiring_soon ?? 0)} expiring soon`}
          loading={loading}
          accent={contracts && contracts.expiring_soon > 0 ? "warn" : "default"}
        />
        <StatCard
          label={`Net lending income (${income?.financial_year ?? "FY"})`}
          value={formatCompactINR(income?.current_fy_net_income ?? 0)}
          hint={`${formatCompactINR(income?.total_net_income ?? 0)} lifetime`}
          loading={loading}
          accent="pos"
        />
      </div>

      {/* Lent out (left out of Zerodha's holdings export, shown from contracts) */}
      <Card>
        <SectionHeader
          title="Lent out"
          subtitle="Shares on loan under active contracts. Zerodha's holdings export leaves these out."
        />
        <div data-testid="lent-out-card">
          {loading ? (
            <TableSkeleton rows={1} cols={4} />
          ) : !lentOut || lentOut.shares === 0 ? (
            <p className="px-4 py-3 text-2xs text-slate-500">No shares are out on loan.</p>
          ) : (
            <div className="px-4 py-3">
              <dl className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <div>
                  <dt className="text-2xs text-slate-500">Stocks</dt>
                  <dd className="text-lg font-semibold text-slate-100 tnum">
                    {formatQty(lentOut.stocks)}
                  </dd>
                </div>
                <div>
                  <dt className="text-2xs text-slate-500">Shares</dt>
                  <dd className="text-lg font-semibold text-slate-100 tnum">
                    {formatQty(lentOut.shares)}
                  </dd>
                </div>
                <div>
                  <dt className="text-2xs text-slate-500">Value at last price</dt>
                  <dd className="text-lg font-semibold text-slate-100 tnum">
                    {formatCompactINR(lentOut.value_at_last_price)}
                  </dd>
                </div>
                <div>
                  <dt className="text-2xs text-slate-500">Fee income due (net)</dt>
                  <dd className="text-lg font-semibold text-pos tnum">
                    {formatINR(lentOut.fee_income_due_net)}
                  </dd>
                  <dd className="text-2xs text-slate-500 tnum">
                    {formatINR(lentOut.fee_income_due_gross)} gross
                  </dd>
                </div>
              </dl>
              {lentOut.unpriced_symbols.length > 0 ? (
                <p className="mt-2 text-2xs text-warn">
                  No last price for {lentOut.unpriced_symbols.join(", ")}; not included in the value.
                </p>
              ) : null}
            </div>
          )}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        {/* Best opportunities */}
        <Card className="xl:col-span-2">
          <SectionHeader
            title="Best lending opportunities"
            subtitle="Actionable Series B bids on your lendable shares, ranked by net yield after Zerodha's charges."
          />
          {loading ? (
            <TableSkeleton rows={4} cols={5} />
          ) : !data || data.best_opportunities.length === 0 ? (
            <EmptyState
              title="No actionable opportunity right now"
              message="An opportunity shows here when a live Series B bid on your lendable shares clears your net-yield floor after charges. See /api/opportunities for every option and why it isn't actionable."
            />
          ) : (
            <div className="space-y-4 p-4">
              <OpportunityYieldChart data={data.best_opportunities} />
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-line">
                      <th className="th">Symbol</th>
                      <th className="th">Series</th>
                      <th className="th text-right" title="Net annualised yield; below it, days to settlement and the net fee">
                        Net yield p.a.
                      </th>
                      <th className="th text-right">Bid</th>
                      <th className="th text-right">Fill</th>
                      <th className="th text-right">Ticket</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.best_opportunities.map((o, i) => (
                      <tr key={`${o.symbol}-${o.series}-${i}`} className="border-b border-line/50">
                        <td className="td font-medium">{o.symbol}</td>
                        <td className="td text-slate-400">{o.series ?? "—"}</td>
                        <td
                          className="td text-right tnum"
                          data-testid={`net-yield-${o.symbol}-${o.series}`}
                        >
                          <span className="font-semibold text-pos">{formatPercent(o.annualized_yield)}</span>
                          <span className="block text-2xs text-slate-500">
                            <span>{o.days_to_settlement ?? "—"} d</span> ·{" "}
                            <span>{formatINR(o.net_fee ?? 0)}</span>
                          </span>
                        </td>
                        <td className="td text-right tnum">₹{formatPrice(o.best_bid)}</td>
                        <td className="td text-right tnum">{formatQty(o.fillable ?? 0)}</td>
                        <td className="td text-right">
                          {o.ticket ? <CopyTicketButton ticket={o.ticket} /> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </Card>

        {/* Upcoming expiries */}
        <Card>
          <SectionHeader
            title="Upcoming expiries"
            subtitle="Active contracts nearing their first-Tuesday expiry."
          />
          {loading ? (
            <TableSkeleton rows={4} cols={2} />
          ) : !data || data.upcoming_expiries.length === 0 ? (
            <EmptyState
              icon="🗓"
              title="No upcoming expiries"
              message="Active lending contracts will appear here as they approach expiry."
            />
          ) : (
            <ul className="divide-y divide-line/60">
              {data.upcoming_expiries.map((e) => (
                <li key={e.contract_id} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-slate-100">{e.symbol}</p>
                    <p className="text-2xs text-slate-500">
                      {formatDate(e.expiry_date)} · {formatQty(e.quantity_lent)} shares
                    </p>
                  </div>
                  <DaysBadge days={e.days_remaining} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* Recent activity */}
      <Card>
        <SectionHeader title="Recent alerts & activity" subtitle="Latest triggered alerts." />
        {loading ? (
          <TableSkeleton rows={3} cols={2} />
        ) : !data || data.recent_alerts.length === 0 ? (
          <EmptyState
            icon="🔔"
            title="No recent activity"
            message="Triggered alerts will show up here as the background monitor evaluates your rules."
          />
        ) : (
          <ul className="divide-y divide-line/60">
            {data.recent_alerts.map((a) => (
              <li key={a.id} className="flex items-center justify-between px-4 py-3">
                <div>
                  <p className="text-sm text-slate-100">{a.message ?? a.alert_type}</p>
                  <p className="text-2xs text-slate-500">{a.alert_type}</p>
                </div>
                <span className="text-2xs text-slate-500">{formatDateTime(a.triggered_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
