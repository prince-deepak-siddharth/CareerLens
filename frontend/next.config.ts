import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    const origin = process.env.CAREERLENS_API_ORIGIN ?? "http://127.0.0.1:8000";
    const resources = ["auth", "profile", "resumes", "jobs", "recommendations", "agent", "evaluation", "ready", "health"];
    return resources.map(resource => ({
      source: `/api/careerlens/${resource}/:path*`, destination: `${origin}/${resource}/:path*`,
    }));
  },
};

export default config;
