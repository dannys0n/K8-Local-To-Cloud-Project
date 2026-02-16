/** @type {import('next').NextConfig} */
const nextConfig = {
  transpilePackages: ["@clerk/nextjs", "@clerk/themes"],
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "utfs.io",
      },
      {
        protocol: "https",
        hostname: "img.clerk.com",
      },
    ],
  },
};

export default nextConfig;
