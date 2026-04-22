import { CloudReaderLogin } from "@/components/CloudReaderLogin";
import { DownloadStream } from "@/components/DownloadStream";

export const dynamic = "force-dynamic";

export default function DownloadsPage() {
  return (
    <div className="stack">
      <CloudReaderLogin />
      <div className="card">
        <DownloadStream />
      </div>
    </div>
  );
}
