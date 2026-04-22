import { DownloadStream } from "@/components/DownloadStream";

export const dynamic = "force-dynamic";

export default function DownloadsPage() {
  return (
    <div className="card">
      <DownloadStream />
    </div>
  );
}
