import Link from 'next/link';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import {
  ArrowRight,
  BookOpen,
  Download,
  History,
  LayoutPanelLeft,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from 'lucide-react';
import { baseOptions, LATEST_TAG, RELEASES_URL } from '@/lib/layout.shared';

const FEATURES = [
  {
    icon: LayoutPanelLeft,
    title: '2패널 집중 작업공간',
    description:
      '왼쪽 58%에 HWPX 라이브 미리보기, 오른쪽 42%에 순수 OMP 터미널을 배치합니다. 문서 전환과 체크포인트 서랍은 Ctrl+B로 필요할 때만 엽니다.',
  },
  {
    icon: RefreshCw,
    title: '한글·OMP 실시간 동기화',
    description:
      '한글에서 직접 저장(Ctrl+S)하거나 OMP가 .hwpx를 수정하면 400ms 디바운스·잠금 해제 대기·ZIP/section0.xml 검증을 거쳐 상주 WebView2에서 쪽·스크롤·배율을 유지한 채 즉시 갱신합니다.',
  },
  {
    icon: History,
    title: '불변 원본 백업 & 체크포인트',
    description:
      '최초 열기 시 .hwpreview/originals/<sha256>.hwpx에 원본을 1회 불변 백업하고, 변경마다 체크포인트를 자동 누적해 언제든 미리보기·복원·별도 내보내기가 가능합니다.',
  },
  {
    icon: ShieldCheck,
    title: '사용자 단위 안전 설치 킷',
    description:
      '%LOCALAPPDATA%\\Programs\\HWPReview에 사용자 단위로 설치되며, 기본 .hwpx 연결·전역 PATH·기존 문서나 인증 정보를 건드리지 않습니다.',
  },
];

const QUICK_LINKS = [
  {
    title: 'Windows 패키지 설치',
    description: '공개 소스로 실행하는 방법과 Windows 키트 공개 후의 단계별 설치 안내',
    href: '/docs/installation',
  },
  {
    title: '필수 외부 도구 5종',
    description: '.NET 10, WebView2, Bun/OMP, PowerShell 7, D2Coding Nerd Font 자동 점검과 수동 확인 방법',
    href: '/docs/installation/prerequisites',
  },
  {
    title: '5분 빠른 시작',
    description: '작업 폴더 열기, 한글에서 편집, OMP 문단 전달, 체크포인트 복원 및 내보내기 실전 흐름',
    href: '/docs/guide/quick-start',
  },
  {
    title: '문제 해결 & FAQ',
    description: '설치 종료 코드(0/1/3/4/5/1602/3010), 미리보기 미갱신, OMP 시작 오류, 폰트 깨짐 대응',
    href: '/docs/troubleshooting',
  },
];

export default function HomePage() {
  return (
    <HomeLayout {...baseOptions()}>
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-16 px-6 py-14 md:py-20">
        {/* Hero */}
        <section className="flex flex-col items-start gap-6">
          <div className="inline-flex items-center gap-2 rounded-full border border-fd-border bg-fd-muted/70 px-3.5 py-1 text-xs font-medium text-fd-muted-foreground">
            <span className="h-2 w-2 rounded-full bg-fd-primary" />
            <span>Windows 10/11 x64 · {LATEST_TAG} 소스 · 설치 ZIP 게시 보류</span>
          </div>

          <h1 className="max-w-3xl text-4xl font-bold tracking-tight text-fd-foreground sm:text-5xl sm:leading-[1.15]">
            HWPX 라이브 미리보기와
            <br />
            OMP 터미널을 한 화면에서
          </h1>

          <p className="max-w-2xl text-base leading-relaxed text-fd-muted-foreground sm:text-lg">
            <strong className="font-semibold text-fd-foreground">HWP Editor</strong>
            (<code className="rounded bg-fd-muted px-1.5 py-0.5 text-sm">HWPReview.exe</code>)는
            Windows에서 <code className="rounded bg-fd-muted px-1.5 py-0.5 text-sm">.hwpx</code>{' '}
            문서를 로컬에서 즉시 렌더링하고, 한글(Hancom)과 <code className="rounded bg-fd-muted px-1.5 py-0.5 text-sm">oh-my-pi (OMP)</code>{' '}
            수정 사항을 실시간으로 반영·백업·복원하는 네이티브 WPF 작업도구입니다.
          </p>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Link
              href="/docs/installation"
              className="inline-flex items-center gap-2 rounded-lg bg-fd-primary px-5 py-2.5 text-sm font-semibold text-fd-primary-foreground shadow-sm transition hover:opacity-90"
            >
              <Download className="h-4 w-4" />
              소스 실행 및 설치 안내
              <ArrowRight className="h-4 w-4" />
            </Link>
            <Link
              href="/docs/guide/quick-start"
              className="inline-flex items-center gap-2 rounded-lg border border-fd-border bg-fd-card px-5 py-2.5 text-sm font-semibold text-fd-foreground transition hover:bg-fd-accent"
            >
              <BookOpen className="h-4 w-4" />
              5분 빠른 시작
            </Link>
            <a
              href={RELEASES_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-lg border border-fd-border px-4 py-2.5 text-sm font-medium text-fd-muted-foreground transition hover:bg-fd-muted hover:text-fd-foreground"
            >
              GitHub Releases 목록
            </a>
          </div>
        </section>

        {/* 2-Panel Workspace Schematic */}
        <section
          aria-label="HWP Editor 2패널 화면 구조"
          className="overflow-hidden rounded-xl border border-fd-border bg-fd-card shadow-sm"
        >
          {/* Slim Top Bar */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-fd-border bg-fd-muted/60 px-4 py-2.5 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 font-medium text-fd-foreground">
                ☰ 서랍 (Ctrl+B)
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 text-fd-muted-foreground">
                폴더 (Ctrl+O)
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2.5 py-0.5 font-mono text-fd-foreground">
                report.hwpx ▾
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 text-fd-foreground">
                한글에서 열기
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 text-fd-muted-foreground">
                ↻ 새로고침
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 text-fd-muted-foreground">
                내보내기
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-0.5 font-medium text-emerald-700 dark:text-emerald-300">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                실시간 감시 중
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 font-medium text-fd-foreground">
                OMP 실행 중
              </span>
              <span className="rounded border border-fd-border bg-fd-background px-2 py-0.5 text-fd-muted-foreground">
                …
              </span>
            </div>
          </div>

          {/* Split Body */}
          <div className="grid grid-cols-1 divide-y divide-fd-border md:grid-cols-12 md:divide-x md:divide-y-0">
            {/* Left: Preview */}
            <div className="flex flex-col gap-3 p-5 md:col-span-7">
              <div className="flex items-center justify-between text-xs text-fd-muted-foreground">
                <span className="font-semibold text-fd-foreground">
                  좌측 패널 (58%) · HWPX 라이브 미리보기 (@rhwp/core WASM)
                </span>
                <span className="font-mono">쪽 · 스크롤 · 배율 유지</span>
              </div>
              <div className="rounded-lg border border-fd-border bg-fd-background p-4 text-sm">
                <div className="mb-2 flex items-center justify-between border-b border-fd-border pb-2 text-xs text-fd-muted-foreground">
                  <span>1 / 12쪽 · 100%</span>
                  <span>문단 더블클릭 → OMP로 위치·발췌 전달</span>
                </div>
                <div className="space-y-2.5 text-xs leading-relaxed text-fd-muted-foreground">
                  <p className="font-semibold text-fd-foreground">
                    1. 사업 추진 개요 및 현황 분석
                  </p>
                  <p className="rounded border border-fd-primary/30 bg-fd-primary/5 p-2 text-fd-foreground">
                    선택 문단 예시 — 더블클릭 시{' '}
                    <code className="text-[11px]">
                      [report.hwpx · 1쪽 구역 0 문단 2]
                    </code>{' '}
                    형식으로 우측 OMP 터미널에 즉시 붙여넣기 됩니다.
                  </p>
                  <p>
                    한글에서 <kbd>Ctrl+S</kbd> 저장 또는 OMP 수정 시 ZIP 및{' '}
                    <code className="text-[11px]">Contents/section0.xml</code> 검증 후
                    WebView2를 재생성하지 않고 in-place로 갱신합니다.
                  </p>
                </div>
              </div>
            </div>

            {/* Right: OMP TUI */}
            <div className="flex flex-col gap-3 bg-fd-muted/25 p-5 md:col-span-5">
              <div className="flex items-center justify-between text-xs text-fd-muted-foreground">
                <span className="inline-flex items-center gap-1.5 font-semibold text-fd-foreground">
                  <Terminal className="h-3.5 w-3.5" />
                  우측 패널 (42%) · 순수 OMP TUI
                </span>
                <span className="font-mono">ConPTY + xterm.js</span>
              </div>
              <div className="flex-1 rounded-lg border border-fd-border bg-zinc-950 p-4 font-mono text-xs leading-relaxed text-zinc-200">
                <div className="text-zinc-400">$ omp  # 작업 폴더에서 바로 시작</div>
                <div className="mt-2 text-sky-300">
                  &gt; [report.hwpx · 1쪽 구역 0 문단 2] &quot;추진 일정 표의 2분기 항목을 최신 기준으로 정리해줘.&quot;
                </div>
                <div className="mt-2 text-emerald-400">
                  ✓ .hwpreview/originals/&lt;sha256&gt;.hwpx 원본 보호됨
                </div>
                <div className="text-emerald-400">
                  ✓ report.hwpx 저장 → 라이브 미리보기 자동 갱신
                </div>
                <div className="text-zinc-400">
                  ✓ 체크포인트 자동 저장 (.hwpreview/history/...)
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Feature Grid */}
        <section className="flex flex-col gap-6">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-fd-foreground">
              핵심 기능 한눈에 보기
            </h2>
            <p className="mt-1 text-sm text-fd-muted-foreground">
              기존 커스텀 채팅과 승인 웹 UI를 걷어내고, 실제 한글 문서 편집 흐름에 필요한 기능만 깔끔하게 통합했습니다.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {FEATURES.map((item) => {
              const Icon = item.icon;
              return (
                <div
                  key={item.title}
                  className="flex flex-col gap-2.5 rounded-xl border border-fd-border bg-fd-card p-5"
                >
                  <div className="inline-flex h-9 w-9 items-center justify-center rounded-lg bg-fd-primary/10 text-fd-primary">
                    <Icon className="h-5 w-5" />
                  </div>
                  <h3 className="text-base font-semibold text-fd-foreground">
                    {item.title}
                  </h3>
                  <p className="text-sm leading-relaxed text-fd-muted-foreground">
                    {item.description}
                  </p>
                </div>
              );
            })}
          </div>
        </section>

        {/* Quick Links */}
        <section className="flex flex-col gap-6">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-fd-foreground">
              문서 바로가기
            </h2>
            <p className="mt-1 text-sm text-fd-muted-foreground">
              설치부터 실전 편집, 체크포인트 복원, 문제 해결까지 순서대로 따라할 수 있습니다.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {QUICK_LINKS.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="group flex flex-col justify-between gap-2 rounded-xl border border-fd-border bg-fd-card p-5 transition hover:border-fd-primary/50 hover:bg-fd-accent/40"
              >
                <div>
                  <h3 className="flex items-center justify-between text-base font-semibold text-fd-foreground">
                    {item.title}
                    <ArrowRight className="h-4 w-4 text-fd-muted-foreground transition group-hover:translate-x-0.5 group-hover:text-fd-primary" />
                  </h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-fd-muted-foreground">
                    {item.description}
                  </p>
                </div>
              </Link>
            ))}
          </div>
        </section>

        {/* Honest Pre-release Notice */}
        <section className="rounded-xl border border-fd-border bg-fd-muted/40 p-5 text-sm text-fd-muted-foreground">
          <p className="font-semibold text-fd-foreground">
            공개 후보의 검증 범위 ({LATEST_TAG})
          </p>
          <p className="mt-1.5 leading-relaxed">
            본 앱은 Windows 10/11 x64용 <strong className="text-fd-foreground">AGPL-3.0-only</strong>{' '}
            소프트웨어입니다. 내장 미리보기는 로컬 <code className="text-xs">@rhwp/core 0.8.7</code>{' '}
            WASM 엔진으로 동작하므로 최종 인쇄·제출 전에는 반드시{' '}
            <strong className="text-fd-foreground">한글(Hancom)</strong>에서 최종 쪽맞춤과 인쇄
            결과를 확인하세요. 자세한 라이선스 및 검증 경계는{' '}
            <Link
              href="/docs/developer/licensing-and-boundaries"
              className="font-medium text-fd-primary underline underline-offset-4"
            >
              라이선스 및 검증 경계
            </Link>{' '}
            문서를 참고하세요.
          </p>
        </section>
      </div>
    </HomeLayout>
  );
}
