export const TARGETS: string[];
export function loadAssets(): {
  version: string;
  tag: string;
  repository: string;
  pythonPackages: string[];
  targets: Record<string, { asset: string; size: number; sha256: string; program: string }>;
};
export function readSingleTarGzFile(buffer: Buffer): { name: string; mode: number; data: Buffer };
export function readZip(buffer: Buffer): {
  name: string;
  mode: number | undefined;
  method: number;
  size: number;
  read(): Buffer;
}[];
