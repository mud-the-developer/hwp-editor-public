import snapshot from '../../public/release.json';

export const repository = 'https://github.com/mud-the-developer/hwp-editor-public';
const { historical, tag, version, commit, executable } = snapshot;
const assets: Record<string, { bytes: number; sha256: string }> = snapshot.assets;
export const release = {
  tag, version, commit, executable, assets,
  label: `${historical ? '과거 릴리스 미리보기' : '공개 사전 릴리스'} · ${tag}`,
  url: `${repository}/releases/tag/${tag}`,
  download: `${repository}/releases/download/${tag}`,
  installer: `HWPReview-win-x64-${version}.zip`,
};
