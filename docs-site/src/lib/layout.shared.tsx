import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';

export const REPO_URL = 'https://github.com/mud-the-developer/hwp-editor-public';
export const LATEST_TAG = 'v1.0.0-rc.4';
export const RELEASES_URL = `${REPO_URL}/releases`;

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <span className="inline-flex items-center gap-2 font-semibold tracking-tight">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
            className="text-fd-primary shrink-0"
          >
            <rect
              x="2.5"
              y="3"
              width="19"
              height="18"
              rx="4"
              stroke="currentColor"
              strokeWidth="2"
            />
            <line x1="13" y1="3" x2="13" y2="21" stroke="currentColor" strokeWidth="1.8" />
            <line x1="5.5" y1="8.5" x2="10.2" y2="8.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            <line x1="5.5" y1="12" x2="10.2" y2="12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            <line x1="5.5" y1="15.5" x2="8.8" y2="15.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            <path d="M16 10l2.2 2L16 14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>HWP Editor</span>
          <span className="rounded-full border border-fd-border bg-fd-muted px-2 py-0.5 text-[11px] font-medium text-fd-muted-foreground">
            {LATEST_TAG}
          </span>
        </span>
      ),
    },
    githubUrl: REPO_URL,
    links: [
      {
        text: '설치 가이드',
        url: '/docs/installation',
        active: 'nested-url',
      },
      {
        text: '사용법',
        url: '/docs/guide/quick-start',
        active: 'nested-url',
      },
      {
        text: '문제 해결',
        url: '/docs/troubleshooting',
        active: 'url',
      },
      {
        text: 'Releases',
        url: RELEASES_URL,
        external: true,
      },
    ],
  };
}
