import { describe, expect, it, vi } from "vitest";
import { registerServiceWorker } from "@/utils/serviceWorker";

describe("registerServiceWorker", () => {
  it("registers /sw.js when the browser supports service workers", async () => {
    const register = vi.fn().mockResolvedValue({});
    const ok = await registerServiceWorker({ serviceWorker: { register } } as unknown as Navigator);
    expect(register).toHaveBeenCalledWith("/sw.js");
    expect(ok).toBe(true);
  });

  it("does nothing (and never throws) without support or on failure", async () => {
    expect(await registerServiceWorker({} as Navigator)).toBe(false);
    const register = vi.fn().mockRejectedValue(new Error("insecure"));
    expect(await registerServiceWorker({ serviceWorker: { register } } as unknown as Navigator)).toBe(false);
  });
});
