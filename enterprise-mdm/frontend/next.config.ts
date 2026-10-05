import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  turbopack: {
    // Explicitly set workspace root so Next.js 16 doesn't pick up the
    // stray package-lock.json on the OneDrive Desktop folder.
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
