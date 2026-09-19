/** @type {import('next').NextConfig} */
const nextConfig = {
  // better-sqlite3 is a native module — don't let webpack try to bundle it.
  serverExternalPackages: ["better-sqlite3"],
};

module.exports = nextConfig;
