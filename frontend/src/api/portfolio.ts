import { http } from "./client";
import type { Holding, PortfolioImportResult, Stock, StockCreate, StockUpdate } from "@/types";

export const portfolioApi = {
  list: (signal?: AbortSignal) => http.get<Holding[]>("/api/portfolio", signal),

  get: (id: number, signal?: AbortSignal) => http.get<Holding>(`/api/portfolio/${id}`, signal),

  create: (payload: StockCreate) => http.post<Stock>("/api/portfolio", payload),

  update: (id: number, payload: StockUpdate) => http.put<Stock>(`/api/portfolio/${id}`, payload),

  remove: (id: number) => http.del<void>(`/api/portfolio/${id}`),

  importCsv: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.postForm<PortfolioImportResult>("/api/portfolio/import", form);
  },
};
