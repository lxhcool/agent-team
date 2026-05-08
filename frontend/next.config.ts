/** @type {import('next').NextConfig} */
const backendPort = process.env.NEXT_PUBLIC_BACKEND_PORT || "8200";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `http://127.0.0.1:${backendPort}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
