/**
 * Cloudflare Worker for Image Caching and Serving
 * 
 * This worker sits in front of R2 and handles:
 * - Cache control headers
 * - CORS headers
 * - Image transformations (optional, requires Cloudflare Images)
 * - Error handling
 */

interface Env {
  FOOD_IMAGES: R2Bucket;
  ENVIRONMENT: string;
}

interface ImageParams {
  size?: 'thumb' | 'medium' | 'full';
  width?: number;
  height?: number;
  quality?: number;
  format?: 'webp' | 'jpeg' | 'avif' | 'auto';
}

// Cache duration by image size (in seconds)
const CACHE_DURATIONS: Record<string, number> = {
  thumb: 31536000,    // 1 year for thumbnails
  thumbnail: 31536000,
  medium: 2592000,     // 30 days for medium
  full: 604800,        // 7 days for full
  default: 86400,      // 1 day default
};

// CORS headers for cross-origin requests
const CORS_HEADERS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Max-Age': '86400',
};

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const pathname = url.pathname;

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: CORS_HEADERS,
      });
    }

    // Only allow GET and HEAD
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response('Method Not Allowed', {
        status: 405,
        headers: { Allow: 'GET, HEAD, OPTIONS' },
      });
    }

    // Parse the path to get the image key
    // Expected format: /foods/thumb/pizza.webp or /foods/medium/pizza.webp
    const key = pathname.startsWith('/') ? pathname.slice(1) : pathname;

    // Skip if not a food image request
    if (!key.startsWith('foods/')) {
      return new Response('Not Found', { status: 404 });
    }

    // Determine the size from the path
    const pathParts = key.split('/');
    const sizeFromPath = pathParts[1] || 'medium';

    try {
      // Try to get from R2
      const object = await env.FOOD_IMAGES.get(key);

      if (!object) {
        return createErrorResponse(404, 'Image not found');
      }

      // Build response headers
      const headers = new Headers();
      
      // Copy R2 metadata headers
      object.writeHttpMetadata(headers);
      headers.set('etag', object.httpEtag);

      // Set cache control based on size
      const cacheDuration = CACHE_DURATIONS[sizeFromPath] || CACHE_DURATIONS.default;
      headers.set('Cache-Control', `public, max-age=${cacheDuration}${sizeFromPath === 'thumb' ? ', immutable' : ''}`);

      // Add CORS headers
      Object.entries(CORS_HEADERS).forEach(([key, value]) => {
        headers.set(key, value);
      });

      // Add custom headers
      headers.set('X-Image-Size', sizeFromPath);
      headers.set('X-Cache-Duration', cacheDuration.toString());
      headers.set('Vary', 'Accept-Encoding');

      // Handle conditional requests
      const ifNoneMatch = request.headers.get('If-None-Match');
      if (ifNoneMatch === object.httpEtag) {
        return new Response(null, {
          status: 304,
          headers,
        });
      }

      // Return the image
      return new Response(object.body, {
        status: 200,
        headers,
      });
    } catch (error) {
      console.error('Error fetching image:', error);
      return createErrorResponse(500, 'Internal Server Error');
    }
  },
};

/**
 * Create a JSON error response
 */
function createErrorResponse(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ error: message, status }),
    {
      status,
      headers: {
        'Content-Type': 'application/json',
        ...CORS_HEADERS,
      },
    }
  );
}

/**
 * Parse image transformation parameters from URL query
 */
function parseImageParams(url: URL): ImageParams {
  const params: ImageParams = {};

  const size = url.searchParams.get('size');
  if (size && ['thumb', 'medium', 'full'].includes(size)) {
    params.size = size as ImageParams['size'];
  }

  const width = parseInt(url.searchParams.get('w') || '');
  if (!isNaN(width) && width > 0 && width <= 2000) {
    params.width = width;
  }

  const height = parseInt(url.searchParams.get('h') || '');
  if (!isNaN(height) && height > 0 && height <= 2000) {
    params.height = height;
  }

  const quality = parseInt(url.searchParams.get('q') || '');
  if (!isNaN(quality) && quality >= 1 && quality <= 100) {
    params.quality = quality;
  }

  const format = url.searchParams.get('f');
  if (format && ['webp', 'jpeg', 'avif', 'auto'].includes(format)) {
    params.format = format as ImageParams['format'];
  }

  return params;
}

/**
 * Generate a Cloudflare Images transformation URL
 * Only works if you have Cloudflare Images enabled
 */
function getTransformUrl(baseUrl: string, key: string, params: ImageParams): string {
  const transformations: string[] = [];

  if (params.width) transformations.push(`width=${params.width}`);
  if (params.height) transformations.push(`height=${params.height}`);
  if (params.quality) transformations.push(`quality=${params.quality}`);
  if (params.format) transformations.push(`format=${params.format}`);

  if (transformations.length === 0) {
    return `${baseUrl}/${key}`;
  }

  return `${baseUrl}/cdn-cgi/image/${transformations.join(',')}/${key}`;
}
