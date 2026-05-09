/**
 * Chart export utilities — PNG (raster), SVG (vector), CSV (data).
 */

/** Export an SVG element as a PNG image via canvas. */
export async function exportChartPNG(
  svgElement: SVGSVGElement,
  filename: string = 'chart.png',
  scale: number = 2,
): Promise<void> {
  const svgData = new XMLSerializer().serializeToString(svgElement);
  const svgBlob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(svgBlob);

  const img = new Image();
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const { width, height } = svgElement.getBoundingClientRect();
  canvas.width = width * scale;
  canvas.height = height * scale;
  ctx.scale(scale, scale);

  return new Promise<void>((resolve) => {
    img.onload = () => {
      // White background
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      ctx.drawImage(img, 0, 0, width, height);
      URL.revokeObjectURL(url);

      canvas.toBlob((blob) => {
        if (blob) downloadBlob(blob, filename);
        resolve();
      }, 'image/png');
    };
    img.src = url;
  });
}

/** Export an SVG element as a .svg file. */
export function exportChartSVG(
  svgElement: SVGSVGElement,
  filename: string = 'chart.svg',
): void {
  const svgData = new XMLSerializer().serializeToString(svgElement);
  const blob = new Blob([svgData], { type: 'image/svg+xml;charset=utf-8' });
  downloadBlob(blob, filename);
}

/** Export chart data as a CSV file. */
export function exportChartCSV(
  data: Array<Record<string, string | number>>,
  filename: string = 'chart-data.csv',
): void {
  if (!data.length) return;

  const headers = Object.keys(data[0]);
  const rows = data.map((row) =>
    headers.map((h) => {
      const val = row[h];
      // Quote strings that contain commas or newlines
      if (typeof val === 'string' && (val.includes(',') || val.includes('\n'))) {
        return `"${val.replace(/"/g, '""')}"`;
      }
      return String(val ?? '');
    }).join(','),
  );

  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  downloadBlob(blob, filename);
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
