import { CloudReaderLogin } from "@/components/CloudReaderLogin";

export const dynamic = "force-dynamic";

export default function Page() {
  return (
    <div className="stack">
      <div className="card stack">
        <div style={{ fontWeight: 600 }}>使い方</div>
        <ol className="muted" style={{ margin: 0, paddingLeft: 20, fontSize: 13 }}>
          <li>下の「ログイン確認」を押すと専用 Chromium が起動します。</li>
          <li>その Chromium で amazon.co.jp にログイン（OTP含む）してください。</li>
          <li>
            もう一度「ログイン確認」を押して「✓ ログイン済み」になったら、蔵書タブで本を選び
            「PDF キャプチャ」を実行します。
          </li>
          <li>
            PDF は <span className="inline-code">/Users/makotofalcon/kindle/</span>{" "}
            に保存されます。
          </li>
        </ol>
      </div>
      <CloudReaderLogin />
    </div>
  );
}
