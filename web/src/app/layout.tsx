import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Kindle Download Helper",
  description: "amazon.co.jp 蔵書を DRM 解除して EPUB で一括ダウンロード",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <div className="container">
          <div className="header">
            <h1>
              Kindle Download Helper
              <small>amazon.co.jp / Mac / EPUB</small>
            </h1>
            <nav className="tabs" aria-label="ページ切替">
              <Link href="/">ログイン</Link>
              <Link href="/library">蔵書</Link>
              <Link href="/downloads">ダウンロード</Link>
            </nav>
          </div>
          {children}
        </div>
      </body>
    </html>
  );
}
