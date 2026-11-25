import './global.css';
import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { Provider } from '@/components/provider';

export const metadata: Metadata = {
  title: {
    template: '%s | HWP Editor 문서',
    default: 'HWP Editor 문서 — Windows HWPX 실시간 편집·미리보기 & OMP 통합',
  },
  description:
    'Windows용 HWP Editor(HWPReview.exe) 설치 가이드, 필수 도구 5종 점검, 2패널 HWPX 라이브 미리보기, 한글·OMP 실시간 동기화, 체크포인트 복원 및 문제 해결 매뉴얼',
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="flex min-h-screen flex-col antialiased">
        <Provider>{children}</Provider>
      </body>
    </html>
  );
}
