import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  // Strict mode catches common React bugs in development
  reactStrictMode: true,

  // API calls are proxied through Next.js in development to avoid CORS
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/:path*`,
      },
    ]
  },
}

export default nextConfig
