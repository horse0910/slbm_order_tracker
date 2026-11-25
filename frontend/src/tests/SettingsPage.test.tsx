import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "@/pages/SettingsPage";
import { ApiError } from "@/api/client";
import type { BusinessSettings } from "@/types";
import { renderWithRouter } from "./testUtils";

vi.mock("@/api/settings", () => ({
  settingsApi: { get: vi.fn(), update: vi.fn(), reset: vi.fn() },
}));
vi.mock("@/api/notifications", () => ({
  notificationsApi: { status: vi.fn(), test: vi.fn() },
}));

import { settingsApi } from "@/api/settings";
import { notificationsApi } from "@/api/notifications";

const DEFAULTS: BusinessSettings = {
  expiring_soon_days: 7,
  default_alert_yield_threshold: 8.0,
  tight_spread_threshold_pct: 5.0,
  above_average_yield_margin_pct: 20.0,
  notifications_enabled: true,
  alert_cooldown_minutes: 45,
  macos_notifications_enabled: true,
  live_refresh_seconds: 180,
  min_net_yield_pct: 4,
  min_fill_qty: 1,
  min_net_fee_inr: 0,
  tracked_expiries: 3,
  rollover_warn_days: 5,
  broker_processing_fee_pct: 20.0,
  gst_pct: 18.0,
  dp_charge_flat_inr: 13,
  default_tax_slab_pct: 30.0,
};

beforeEach(() => {
  vi.mocked(notificationsApi.status).mockResolvedValue({
    macos_available: true,
    macos_enabled: true,
    whatsapp_configured: false,
    email_configured: false,
  });
  vi.mocked(settingsApi.get).mockResolvedValue({ ...DEFAULTS });
  vi.mocked(settingsApi.update).mockResolvedValue({ ...DEFAULTS });
  vi.mocked(settingsApi.reset).mockResolvedValue({ ...DEFAULTS });
});

describe("SettingsPage", () => {
  it("loads the effective config into an editable form", async () => {
    renderWithRouter(<SettingsPage />);
    expect(await screen.findByLabelText("Expiring-soon window (days)")).toHaveValue(7);
    expect(screen.getByLabelText("GST (%)")).toHaveValue(18);
    expect(screen.getByLabelText("Live board refresh (seconds)")).toHaveValue(180);
    expect(screen.getByLabelText("DP charge per scrip (₹, before GST)")).toHaveValue(13);
    expect(screen.queryByLabelText("DP charge (%)")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Notifications enabled")).toBeChecked();
    // Nothing changed yet → Save is disabled.
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("saves a changed setting via PUT and confirms success", async () => {
    const user = userEvent.setup();
    vi.mocked(settingsApi.update).mockResolvedValue({ ...DEFAULTS, expiring_soon_days: 14 });
    renderWithRouter(<SettingsPage />);

    const days = await screen.findByLabelText("Expiring-soon window (days)");
    await user.clear(days);
    await user.type(days, "14");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(settingsApi.update).toHaveBeenCalledWith(
      expect.objectContaining({ expiring_soon_days: 14 }),
    );
    expect(await screen.findByText(/take effect immediately/i)).toBeInTheDocument();
    // Baseline updated → the form is clean again, so Save re-disables.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled(),
    );
  });

  it("resets every override via DELETE", async () => {
    const user = userEvent.setup();
    renderWithRouter(<SettingsPage />);

    const days = await screen.findByLabelText("Expiring-soon window (days)");
    await user.clear(days);
    await user.type(days, "30");
    await user.click(screen.getByRole("button", { name: "Reset to defaults" }));

    expect(settingsApi.reset).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/reset to environment defaults/i)).toBeInTheDocument();
    expect(screen.getByLabelText("Expiring-soon window (days)")).toHaveValue(7);
  });

  it("surfaces a backend validation error and does not confirm success", async () => {
    const user = userEvent.setup();
    vi.mocked(settingsApi.update).mockRejectedValue(
      new ApiError("validation", "gst_pct must be at most 100", 422),
    );
    renderWithRouter(<SettingsPage />);

    const gst = await screen.findByLabelText("GST (%)");
    await user.clear(gst);
    await user.type(gst, "150");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(/must be at most 100/i)).toBeInTheDocument();
    expect(screen.queryByText(/take effect immediately/i)).not.toBeInTheDocument();
  });

  it("PUTs only the toggled notifications flag, not the whole form", async () => {
    const user = userEvent.setup();
    vi.mocked(settingsApi.update).mockResolvedValue({ ...DEFAULTS, notifications_enabled: false });
    renderWithRouter(<SettingsPage />);

    const toggle = await screen.findByLabelText("Notifications enabled");
    await user.click(toggle); // true -> false
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    // Diff-send: exactly the changed key, nothing else.
    expect(settingsApi.update).toHaveBeenCalledWith({ notifications_enabled: false });
    expect(await screen.findByText(/take effect immediately/i)).toBeInTheDocument();
  });

  it("keeps Save disabled while a numeric field is blank (invalid)", async () => {
    const user = userEvent.setup();
    renderWithRouter(<SettingsPage />);

    const days = await screen.findByLabelText("Expiring-soon window (days)");
    await user.clear(days); // empty -> NaN -> invalid, even though the form is now dirty
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(settingsApi.update).not.toHaveBeenCalled();
  });

  it("maps a field-level backend error to the offending input", async () => {
    const user = userEvent.setup();
    vi.mocked(settingsApi.update).mockRejectedValue(
      new ApiError("validation", "gst_pct: bad gst value", 422, [
        { field: "gst_pct", message: "bad gst value" },
      ]),
    );
    renderWithRouter(<SettingsPage />);

    const gst = await screen.findByLabelText("GST (%)");
    await user.clear(gst);
    await user.type(gst, "42");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    // The inline, field-scoped message renders; no duplicate general banner.
    expect(await screen.findByText("bad gst value")).toBeInTheDocument();
    expect(screen.queryByText(/take effect immediately/i)).not.toBeInTheDocument();
  });
});

describe("SettingsPage test notification (Phase 5.5)", () => {
  it("sends a test notification and shows each channel's outcome", async () => {
    const user = userEvent.setup();
    vi.mocked(notificationsApi.test).mockResolvedValue({
      results: [
        { channel: "macos", delivered: true, outcome: "sent", detail: "Shown as a macOS notification." },
        { channel: "whatsapp", delivered: false, outcome: "skipped", detail: "skipped: WhatsApp not configured" },
      ],
    });
    renderWithRouter(<SettingsPage />);
    expect(await screen.findByText(/WhatsApp: not configured/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send a test notification" }));
    expect(notificationsApi.test).toHaveBeenCalled();
    expect(await screen.findByText(/macOS: sent/)).toBeInTheDocument();
    expect(screen.getByText(/WhatsApp: skipped/)).toBeInTheDocument();
  });
});

describe("SettingsPage settings from Phases 2-5 (Phase 6.6)", () => {
  it("shows every new setting with its value", async () => {
    renderWithRouter(<SettingsPage />);
    expect(await screen.findByLabelText("Net yield floor (%)")).toHaveValue(4);
    expect(screen.getByLabelText("Minimum fill (shares)")).toHaveValue(1);
    expect(screen.getByLabelText("Minimum net fee (₹)")).toHaveValue(0);
    expect(screen.getByLabelText("Expiries shown per stock")).toHaveValue(3);
    expect(screen.getByLabelText("Rollover warning (days before reverse leg)")).toHaveValue(5);
    expect(screen.getByLabelText("Alert cooldown (minutes)")).toHaveValue(45);
    expect(screen.getByLabelText("Live board refresh (seconds)")).toHaveValue(180);
    expect(screen.getByLabelText("DP charge per scrip (₹, before GST)")).toHaveValue(13);
    expect(screen.getByLabelText("macOS notifications")).toBeChecked();
  });

  it("saves only what changed", async () => {
    const user = userEvent.setup();
    vi.mocked(settingsApi.update).mockResolvedValue({ ...DEFAULTS, min_net_yield_pct: 6, macos_notifications_enabled: false });
    renderWithRouter(<SettingsPage />);
    const floor = await screen.findByLabelText("Net yield floor (%)");
    await user.clear(floor);
    await user.type(floor, "6");
    await user.click(screen.getByLabelText("macOS notifications"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(settingsApi.update).toHaveBeenCalledWith({ min_net_yield_pct: 6, macos_notifications_enabled: false });
  });
});
