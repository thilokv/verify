/** @type {import('next').NextConfig} */
export default {
  async rewrites() {
    // Proxy API calls to FastAPI so the browser sees one origin.
    return [{
      source: "/api/:path*",
      destination: (process.env.API_URL || "http://localhost:8732") + "/api/:path*",
    }];
  },
};
