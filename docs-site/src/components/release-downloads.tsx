import { release, repository } from '@/lib/release';

export function ReleaseLink() {
  return <a href={release.url}>{release.label}</a>;
}

export function InstallerHashCommand() {
  return <pre><code>{`Get-FileHash -LiteralPath '.\\${release.installer}' -Algorithm SHA256`}</code></pre>;
}

export function ReleaseDownloads() {
  const files = [
    [release.installer, 'Windows x64 설치 키트'],
    ['SHA256SUMS.txt', '같은 릴리스에 첨부된 파일의 체크섬'],
    [`HWPReview-source-${release.version}.zip`, '해당 앱을 빌드하는 데 사용한 소스'],
    [`HWPReview-corresponding-source-${release.version}.zip`, '앱 빌드 입력과 상류 소스·출처 자료'],
    ['SOURCE-MATERIALS.txt', '대응 소스에 포함된 자료 안내'],
    ['RELEASE-MANIFEST.json', '소스 커밋·실행 파일·첨부 파일의 검증 기록'],
  ];
  return (
    <>
      <p><ReleaseLink /> · 소스 커밋 <a href={`${repository}/commit/${release.commit}`}><code>{release.commit}</code></a></p>
      <table>
        <thead><tr><th>다운로드</th><th>용도</th><th>크기(바이트)</th></tr></thead>
        <tbody>{files.map(([name, purpose]) => (
          <tr key={name}>
            <td><a href={`${release.download}/${name}`}>{name}</a></td><td>{purpose}</td>
            <td>{release.assets[name].bytes.toLocaleString('ko-KR')}</td>
          </tr>
        ))}</tbody>
      </table>
      <p>Windows 설치 ZIP의 SHA-256: <code className="break-all">{release.assets[release.installer].sha256}</code></p>
      <p>압축을 푼 <code>HWPReview.exe</code>의 SHA-256: <code className="break-all">{release.executable.sha256}</code></p>
    </>
  );
}
