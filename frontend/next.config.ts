import type { NextConfig } from 'next'
import path from 'path'
import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

const nextConfig: NextConfig = {
  // Fix workspace root detection - ignore parent package.json files
  outputFileTracingRoot: path.join(__dirname, './'),
  // Move dev indicator out of sidebar area
  devIndicators: false,
  experimental: {
    serverActions: {
      bodySizeLimit: '2mb',
    },
    // Enable optimized package imports for better tree-shaking
    // Note: @phosphor-icons/react removed due to vendor chunk issues with Next.js 15
    optimizePackageImports: [
      'lucide-react',
      '@radix-ui/react-icons',
      'date-fns',
    ],
  },
  // Compiler optimizations
  compiler: {
    // Remove console.log in production
    removeConsole: process.env.NODE_ENV === 'production' ? {
      exclude: ['error', 'warn'],
    } : false,
  },

  // Image optimization
  images: {
    formats: ['image/avif', 'image/webp'],
    deviceSizes: [640, 750, 828, 1080, 1200, 1920],
  },
  // Headers for caching - different strategies for dev vs production
  async headers() {
    const isDev = process.env.NODE_ENV !== 'production'

    return [
      // In development, disable caching for HTML pages to always get fresh content
      ...(isDev ? [
        {
          source: '/:path*',
          headers: [
            {
              key: 'Cache-Control',
              value: 'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0',
            },
            {
              key: 'Pragma',
              value: 'no-cache',
            },
            {
              key: 'Expires',
              value: '0',
            },
          ],
        },
      ] : []),
      // Static images can be cached (these have content hashes)
      {
        source: '/:all*(svg|jpg|png|webp|avif)',
        headers: [
          {
            key: 'Cache-Control',
            value: isDev
              ? 'no-store, no-cache, must-revalidate, max-age=0'
              : 'public, max-age=31536000, immutable',
          },
        ],
      },
      // Next.js static chunks have content hashes, so can be cached in production
      {
        source: '/_next/static/:path*',
        headers: [
          {
            key: 'Cache-Control',
            value: isDev
              ? 'no-store, no-cache, must-revalidate, max-age=0'
              : 'public, max-age=31536000, immutable',
          },
        ],
      },
    ]
  },
}

export default withMDX(nextConfig)
