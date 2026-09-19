/** @type {import('next').NextConfig} */
const nextConfig = {
  // better-sqlite3 is a native module — don't let webpack try to bundle it.
  // (Next.js 15 renamed this to top-level `serverExternalPackages`; on 14.x
  // it still lives under `experimental`.)
  experimental: {
    serverComponentsExternalPackages: ["better-sqlite3"],
  },
};

module.exports = nextConfig;
