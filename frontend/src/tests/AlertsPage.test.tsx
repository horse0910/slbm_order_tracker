import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { AlertsPage } from "@/pages/AlertsPage";
import { LiveAlertsProvider } from "@/context/LiveAlertsProvider";
import { renderWithRouter, makeAlert, makeHolding } from "./testUtils";
import { FakeWebSocket } from "./fakeWebSocket";

vi.mock("@/api/alerts", () => ({
  alertsApi: {
    list: vi.fn(),
    create: vi.fn(),
    setActive: vi.fn(),
    remove: vi.fn(),
    evaluate: vi.fn(),
    deliveries: vi.fn(),
  },
}));
vi.mock("@/api/portfolio", () => ({
  portfolioApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), importCsv: vi.fn() },
}));

import { alertsApi } from "@/api/alerts";
import { portfolioApi } from "@/api/portfolio";

let OriginalWebSocket: typeof WebSocket;

beforeEach(() => {
  vi.mocked(alertsApi.list).mockResolvedValue([makeAlert()]);
  vi.mocked(alertsApi.deliveries).mockResolvedValue({ email_channel_active: false, deliveries: [] });
  vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding()]);
  OriginalWebSocket = globalThis.WebSocket;
  FakeWebSocket.reset();
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
});

afterEach(() => {
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = OriginalWebSocket;
});

describe("AlertsPage", () => {
  it("lists configured rules with their state", async () => {
    renderWithRouter(<AlertsPage />);
    expect(await screen.findByText("Gross yield threshold")).toBeInTheDocument();
    expect(screen.getByText(/Armed/)).toBeInTheDocument();
  });

  it("creates a rule", async () => {
    const user = userEvent.setup();
    vi.mocked(alertsApi.create).mockResolvedValue(makeAlert({ id: 2, alert_type: "TIGHT_SPREAD" }));
    renderWithRouter(<AlertsPage />);
    await screen.findByText("Gross yield threshold");

    await user.click(screen.getByRole("button", { name: /new rule/i }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(/alert type/i), "TIGHT_SPREAD");
    await user.type(within(dialog).getByLabelText(/max spread/i), "4");
    await user.click(within(dialog).getByRole("button", { name: /create rule/i }));

    await waitFor(() =>
      expect(alertsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ alert_type: "TIGHT_SPREAD", threshold: 4 }),
      ),
    );
  });

  it("runs an evaluation and reports the result", async () => {
    const user = userEvent.setup();
    vi.mocked(alertsApi.evaluate).mockResolvedValue({
      evaluated: 1,
      fired: 1,
      results: [],
      deliveries: [],
    });
    renderWithRouter(<AlertsPage />);
    await screen.findByText("Gross yield threshold");

    await user.click(screen.getByRole("button", { name: /evaluate now/i }));
    expect(await screen.findByText(/Evaluated 1 rule\(s\); 1 newly triggered/)).toBeInTheDocument();
  });

  it("shows a triggered rule's message", async () => {
    vi.mocked(alertsApi.list).mockResolvedValue([
      makeAlert({ is_triggered: true, trigger_message: "RELIANCE yield 9.00% ≥ threshold 8.00%." }),
    ]);
    renderWithRouter(<AlertsPage />);
    expect(await screen.findByText(/RELIANCE yield 9.00%/)).toBeInTheDocument();
    expect(screen.getByText(/Triggered/)).toBeInTheDocument();
  });

  it("goes Live and refetches when a real-time alert_fired event arrives", async () => {
    render(
      <MemoryRouter>
        <LiveAlertsProvider>
          <AlertsPage />
        </LiveAlertsProvider>
      </MemoryRouter>,
    );
    await screen.findByText("Gross yield threshold");

    // Before the socket opens the connection pill reads Offline.
    expect(screen.getByText(/Offline/)).toBeInTheDocument();
    const listCallsBefore = vi.mocked(alertsApi.list).mock.calls.length;

    const socket = FakeWebSocket.instances[0];
    act(() => socket._open());
    expect(await screen.findByText(/🟢 Live/)).toBeInTheDocument();

    // A pushed alert_fired frame triggers an authoritative REST refetch (never a
    // client-side recompute) and surfaces an in-app toast with the backend message.
    act(() =>
      socket._message({
        type: "alert_fired",
        alert_id: 1,
        alert_type: "YIELD_THRESHOLD",
        stock_id: null,
        message: "NIFTYBEES yield 9.10% ≥ threshold 8.00%.",
        last_triggered_at: "2026-09-24T10:30:00",
      }),
    );

    await waitFor(() =>
      expect(vi.mocked(alertsApi.list).mock.calls.length).toBeGreaterThan(listCallsBefore),
    );
    expect(await screen.findByText(/NIFTYBEES yield 9\.10%/)).toBeInTheDocument();
  });

  it("shows the email-off badge and an empty delivery log by default", async () => {
    renderWithRouter(<AlertsPage />);
    await screen.findByText("Gross yield threshold");
    expect(screen.getByText("✉ Email off")).toBeInTheDocument();
    expect(screen.getByText("Delivery log")).toBeInTheDocument();
    expect(screen.getByText("No deliveries yet")).toBeInTheDocument();
  });

  it("renders the delivery log and email-on badge when email is configured", async () => {
    vi.mocked(alertsApi.deliveries).mockResolvedValue({
      email_channel_active: true,
      deliveries: [
        {
          id: 2,
          alert_id: 1,
          channel: "email",
          delivered: true,
          detail: "Emailed to alerts@example.com.",
          created_at: "2026-09-25T10:00:00",
        },
        {
          id: 1,
          alert_id: 1,
          channel: "in_app",
          delivered: true,
          detail: "Surfaced in-app (dashboard recent-alerts / Alerts page).",
          created_at: "2026-09-25T10:00:00",
        },
      ],
    });
    renderWithRouter(<AlertsPage />);
    await screen.findByText("Gross yield threshold");

    expect(await screen.findByText("✉ Email on")).toBeInTheDocument();
    expect(screen.getByText("Emailed to alerts@example.com.")).toBeInTheDocument();
    expect(screen.getByText("Email")).toBeInTheDocument(); // channel cell
    expect(screen.getByText("In-app")).toBeInTheDocument();
    expect(screen.getAllByText("✓ Sent")).toHaveLength(2);
  });
});

describe("AlertsPage lending opportunity (Phase 3.6)", () => {
  it("offers the LENDING_OPPORTUNITY rule with a net-yield floor", async () => {
    const user = userEvent.setup();
    renderWithRouter(<AlertsPage />);
    await screen.findByText("Gross yield threshold");
    await user.click(screen.getByRole("button", { name: /new rule/i }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText(/alert type/i), "LENDING_OPPORTUNITY");
    expect(within(dialog).getByLabelText(/min net yield/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/at the live Series B bid/i)).toBeInTheDocument();
  });
});
