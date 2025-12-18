/**
 * Cloudflare R2 Upload Service
 * 
 * Backend service for uploading and managing food images on Cloudflare R2
 * This should be deployed on your backend (Node.js/Python)
 */

import {
  S3Client,
  PutObjectCommand,
  DeleteObjectCommand,
  GetObjectCommand,
  HeadObjectCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import sharp from 'sharp';
import { encode as encodeBlurhash } from 'blurhash';

// ============================================================================
// CONFIGURATION
// ============================================================================

interface R2Config {
  accountId: string;
  accessKeyId: string;
  secretAccessKey: string;
  bucketName: string;
  publicUrl: string;
}

const config: R2Config = {
  accountId: process.env.CLOUDFLARE_ACCOUNT_ID!,
  accessKeyId: process.env.R2_ACCESS_KEY_ID!,
  secretAccessKey: process.env.R2_SECRET_ACCESS_KEY!,
  bucketName: process.env.R2_BUCKET_NAME || 'auvra-food-images',
  publicUrl: process.env.R2_PUBLIC_URL || 'https://pub-xxxxx.r2.dev',
};

// Image size configurations
const IMAGE_SIZES = {
  thumbnail: { width: 150, height: 150, quality: 75 },
  medium: { width: 400, height: 400, quality: 80 },
  full: { width: 800, height: 800, quality: 85 },
} as const;

type ImageSize = keyof typeof IMAGE_SIZES;

// ============================================================================
// S3 CLIENT SETUP
// ============================================================================

const s3Client = new S3Client({
  region: 'auto',
  endpoint: `https://${config.accountId}.r2.cloudflarestorage.com`,
  credentials: {
    accessKeyId: config.accessKeyId,
    secretAccessKey: config.secretAccessKey,
  },
});

// ============================================================================
// IMAGE PROCESSING
// ============================================================================

interface ProcessedImage {
  buffer: Buffer;
  width: number;
  height: number;
  size: number;
}

/**
 * Process an image to a specific size
 */
async function processImage(
  inputBuffer: Buffer,
  targetSize: ImageSize
): Promise<ProcessedImage> {
  const { width, height, quality } = IMAGE_SIZES[targetSize];

  const processed = await sharp(inputBuffer)
    .resize(width, height, {
      fit: 'cover',
      position: 'center',
    })
    .webp({ quality })
    .toBuffer({ resolveWithObject: true });

  return {
    buffer: processed.data,
    width: processed.info.width,
    height: processed.info.height,
    size: processed.info.size,
  };
}

/**
 * Generate blurhash from image buffer
 */
async function generateBlurhash(imageBuffer: Buffer): Promise<string> {
  const { data, info } = await sharp(imageBuffer)
    .resize(32, 32, { fit: 'cover' })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });

  return encodeBlurhash(
    new Uint8ClampedArray(data),
    info.width,
    info.height,
    4, // componentX
    3  // componentY
  );
}

// ============================================================================
// UPLOAD FUNCTIONS
// ============================================================================

interface UploadResult {
  success: boolean;
  imageId: string;
  urls: {
    thumbnail: string;
    medium: string;
    full: string;
  };
  blurhash: string;
  sizes: {
    thumbnail: number;
    medium: number;
    full: number;
  };
}

/**
 * Upload a food image with all size variants
 */
export async function uploadFoodImage(
  imageBuffer: Buffer,
  imageId: string,
  contentType: string = 'image/webp'
): Promise<UploadResult> {
  // Ensure imageId ends with .webp
  const cleanImageId = imageId.replace(/\.[^.]+$/, '') + '.webp';

  const urls: UploadResult['urls'] = {
    thumbnail: '',
    medium: '',
    full: '',
  };

  const sizes: UploadResult['sizes'] = {
    thumbnail: 0,
    medium: 0,
    full: 0,
  };

  // Process and upload each size
  for (const size of Object.keys(IMAGE_SIZES) as ImageSize[]) {
    const processed = await processImage(imageBuffer, size);

    const key = `foods/${size}/${cleanImageId}`;

    await s3Client.send(
      new PutObjectCommand({
        Bucket: config.bucketName,
        Key: key,
        Body: processed.buffer,
        ContentType: 'image/webp',
        CacheControl: getCacheControl(size),
        Metadata: {
          width: processed.width.toString(),
          height: processed.height.toString(),
          originalSize: size,
        },
      })
    );

    urls[size] = `${config.publicUrl}/${key}`;
    sizes[size] = processed.size;
  }

  // Generate blurhash from thumbnail
  const thumbnailBuffer = (await processImage(imageBuffer, 'thumbnail')).buffer;
  const blurhash = await generateBlurhash(thumbnailBuffer);

  return {
    success: true,
    imageId: cleanImageId,
    urls,
    blurhash,
    sizes,
  };
}

/**
 * Get appropriate cache control header for image size
 */
function getCacheControl(size: ImageSize): string {
  switch (size) {
    case 'thumbnail':
      // Thumbnails rarely change - cache for 1 year
      return 'public, max-age=31536000, immutable';
    case 'medium':
      // Medium images - cache for 30 days
      return 'public, max-age=2592000';
    case 'full':
      // Full images - cache for 7 days
      return 'public, max-age=604800';
    default:
      return 'public, max-age=86400';
  }
}

/**
 * Delete a food image and all its variants
 */
export async function deleteFoodImage(imageId: string): Promise<boolean> {
  const cleanImageId = imageId.replace(/\.[^.]+$/, '') + '.webp';

  try {
    for (const size of Object.keys(IMAGE_SIZES) as ImageSize[]) {
      const key = `foods/${size}/${cleanImageId}`;
      await s3Client.send(
        new DeleteObjectCommand({
          Bucket: config.bucketName,
          Key: key,
        })
      );
    }
    return true;
  } catch (error) {
    console.error('Failed to delete image:', error);
    return false;
  }
}

/**
 * Check if an image exists
 */
export async function imageExists(imageId: string): Promise<boolean> {
  const cleanImageId = imageId.replace(/\.[^.]+$/, '') + '.webp';
  const key = `foods/medium/${cleanImageId}`;

  try {
    await s3Client.send(
      new HeadObjectCommand({
        Bucket: config.bucketName,
        Key: key,
      })
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * Generate a presigned URL for direct upload (client-side upload)
 */
export async function getPresignedUploadUrl(
  imageId: string,
  size: ImageSize = 'full',
  expiresIn: number = 3600
): Promise<string> {
  const cleanImageId = imageId.replace(/\.[^.]+$/, '') + '.webp';
  const key = `foods/${size}/${cleanImageId}`;

  const command = new PutObjectCommand({
    Bucket: config.bucketName,
    Key: key,
    ContentType: 'image/webp',
    CacheControl: getCacheControl(size),
  });

  return getSignedUrl(s3Client, command, { expiresIn });
}

/**
 * Get a presigned URL for downloading an image
 */
export async function getPresignedDownloadUrl(
  imageId: string,
  size: ImageSize = 'full',
  expiresIn: number = 3600
): Promise<string> {
  const cleanImageId = imageId.replace(/\.[^.]+$/, '') + '.webp';
  const key = `foods/${size}/${cleanImageId}`;

  const command = new GetObjectCommand({
    Bucket: config.bucketName,
    Key: key,
  });

  return getSignedUrl(s3Client, command, { expiresIn });
}

// ============================================================================
// BULK OPERATIONS
// ============================================================================

interface BulkUploadResult {
  successful: string[];
  failed: { imageId: string; error: string }[];
}

/**
 * Upload multiple food images
 */
export async function bulkUploadFoodImages(
  images: { buffer: Buffer; imageId: string }[]
): Promise<BulkUploadResult> {
  const results: BulkUploadResult = {
    successful: [],
    failed: [],
  };

  // Process in batches of 5 to avoid overwhelming the API
  const batchSize = 5;
  for (let i = 0; i < images.length; i += batchSize) {
    const batch = images.slice(i, i + batchSize);

    const batchResults = await Promise.allSettled(
      batch.map(({ buffer, imageId }) => uploadFoodImage(buffer, imageId))
    );

    batchResults.forEach((result, index) => {
      const { imageId } = batch[index];
      if (result.status === 'fulfilled') {
        results.successful.push(imageId);
      } else {
        results.failed.push({
          imageId,
          error: result.reason?.message || 'Unknown error',
        });
      }
    });
  }

  return results;
}

// ============================================================================
// EXPORTS
// ============================================================================

export {
  IMAGE_SIZES,
  processImage,
  generateBlurhash,
  getCacheControl,
};

export type { UploadResult, BulkUploadResult, ImageSize };
