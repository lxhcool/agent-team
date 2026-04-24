/** @type {import('next').NextConfig} */
const backendPort = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `http://localhost:${backendPort}/api/:path*`,
      },
      // Alias: /execution/:id -> /executions/:id (per requirements design doc)
      {
        source: "/execution/:id",
        destination: "/executions/:id",
      },
    ];
  },
};

export default nextConfig;
