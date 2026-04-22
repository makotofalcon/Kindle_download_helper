import type { NextConfig } from "next";

const API_ORIGIN = process.env.KINDLE_WEB_API_ORIGIN ?? "http://127.0.0.1:8001";

const nextConfig: NextConfig = {
  // フロント (localhost:3000) から /api/* で叩かれたリクエストを FastAPI にフォワード
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/api/:path*`,
      },
    ];
  },
  // dev サーバへ 127.0.0.1 からのアクセスを許可
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  reactStrictMode: true,
};

export default nextConfig;
