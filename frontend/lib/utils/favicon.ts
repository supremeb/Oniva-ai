/**
 * Favicon utilities for source citation rendering.
 */

export function getFaviconUrl(url: string, size: 16 | 32 = 32): string {
  try {
    const domain = new URL(url).hostname;
    return `https://www.google.com/s2/favicons?domain=${domain}&sz=${size}`;
  } catch {
    return '';
  }
}

export function isValidSourceUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}
