import { exportApi } from "@/api/export";

interface ExportCsvButtonProps {
  /** Fully-built backend CSV URL (see `exportApi.*Url()`). */
  url: string;
  label?: string;
  title?: string;
  className?: string;
}

/**
 * Ghost button that downloads a backend CSV export. The backend sets the saved
 * filename via `Content-Disposition`; this only opens the download. Marked
 * `no-print` so it never appears in a "Save as PDF" printout.
 */
export function ExportCsvButton({
  url,
  label = "Export CSV",
  title,
  className = "btn-ghost no-print",
}: ExportCsvButtonProps) {
  return (
    <button
      type="button"
      className={className}
      title={title}
      onClick={() => exportApi.download(url)}
    >
      {label}
    </button>
  );
}
