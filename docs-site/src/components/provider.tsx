'use client';

import type { ReactNode } from 'react';
import { RootProvider } from 'fumadocs-ui/provider/next';

const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';

const KO_TRANSLATIONS: Record<string, string> = {
  Search: '문서 검색',
  'On this page': '이 페이지 목차',
  'Table of Contents': '목차',
  'No Headings': '목차 없음',
  'Next Page': '다음 페이지',
  'Previous Page': '이전 페이지',
  'Last updated on': '마지막 업데이트',
  'Choose a language': '언어 선택',
  'Edit on GitHub': 'GitHub에서 편집',
  'Hide Sidebar': '사이드바 숨기기',
  'Skip to Content': '본문으로 건너뛰기',
  'Back to Home': '홈으로 돌아가기',
  'Page Not Found': '페이지를 찾을 수 없습니다',
  'The page you are looking for might have been removed, had its name changed, or is temporarily unavailable.':
    '요청하신 페이지가 이동되었거나 삭제되었을 수 있습니다.',
  'Copy Markdown': 'Markdown 복사',
  'Copied Markdown': '복사 완료',
  'View as Markdown': 'Markdown 보기',
};

export function Provider({ children }: { children: ReactNode }) {
  return (
    <RootProvider
      i18n={{
        locale: 'ko',
        translations: KO_TRANSLATIONS,
      }}
      search={{
        options: {
          type: 'static',
          api: `${basePath}/api/search.json`,
        },
        links: [
          ['Windows 패키지 설치 가이드', '/docs/installation'],
          ['필수 외부 도구 5종', '/docs/installation/prerequisites'],
          ['5분 빠른 시작', '/docs/guide/quick-start'],
          ['한글·OMP 실시간 동기화', '/docs/guide/live-sync'],
          ['문제 해결 & FAQ', '/docs/troubleshooting'],
        ],
      }}
    >
      {children}
    </RootProvider>
  );
}
