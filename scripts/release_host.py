"""Per-user Windows release host. Never fetch or execute PR heads; never run Hancom unattended."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from urllib.parse import urlencode
from urllib.request import urlopen

REPO = 'mud-the-developer/hwp-editor-public'
URL = 'https://github.com/' + REPO
FULL = re.compile('[0-9a-f]{40}')
HEX = re.compile('[0-9a-f]{64}')
RC = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-rc\.[1-9][0-9]*')
REQUIRED = {'check-0', 'check-1', 'frozen-candidate', 'docs-preview'}
WRAPPERS = ('__init__.py', 'dicts.dat', '7D2B6F3C-1D95-4E0C-BF5A-5EE564186FBCx0x1x0.py')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    tmp = path.with_suffix('.new')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2)
    os.replace(tmp, path)


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def host_env():
    # No inherited API keys, GH_TOKEN or provider configuration. gh uses the operator's credential manager.
    keys = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC', 'OS', 'TEMP', 'TMP', 'USERNAME',
            'USERDOMAIN', 'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'ALLUSERSPROFILE',
            'HOMEDRIVE', 'HOMEPATH', 'SYSTEMDRIVE', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432',
            'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'PSMODULEPATH', 'COMPUTERNAME'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keys}
    env.update(GH_PROMPT_DISABLED='1', GIT_TERMINAL_PROMPT='0', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    return env


def command(args, **kwargs):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          encoding='utf-8', errors='replace', check=True, timeout=60, env=host_env(), **kwargs).stdout.strip()


def git(c, *args):
    return command([c['git'], '--no-replace-objects', '-C', c['repo'], *args])


def gh(c, *args):
    return command([c['gh'], *args])


def api(c, path):
    return json.loads(gh(c, 'api', 'repos/' + REPO + '/' + path))


def main_sha(c):
    require(git(c, 'remote', 'get-url', 'origin').removesuffix('.git') == URL, 'Not the enrolled public repository')
    return api(c, 'git/ref/heads/main')['object']['sha']


def accepted(c, sha):
    require(FULL.fullmatch(sha), 'A full commit is required')
    return subprocess.run([c['git'], '--no-replace-objects', '-C', c['repo'], 'merge-base', '--is-ancestor',
                           sha, 'refs/heads/main'], env=host_env(), capture_output=True, timeout=15).returncode == 0


@contextmanager
def lock(path):
    import msvcrt
    with path.open('a+b') as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
        else:
            try:
                yield True
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def identity(run, sha):
    row = load(run / 'built.json')
    require(row['commit'] == sha and HEX.fullmatch(row['kit_sha256']) and HEX.fullmatch(row['exe_sha256'])
            and RC.fullmatch(row['version']), 'Only a bound prerelease build may be published')
    return row


def state(run, sha, stage, **details):
    save(run / 'state.json', {'commit': sha, 'stage': stage, 'at': now(), **details})
    print(f'{sha[:12]}: {stage}', flush=True)


def candidate(c, run, sha, step, *args):
    src = run / 'src'
    env = host_env()
    env['DOTNET_ROOT_X64'] = str(Path(c['dotnet']).parent)
    env['PATH'] = os.pathsep.join([str(Path(c[k]).parent) for k in ('dotnet', 'git')] + [env['PATH']])
    temp = run / ('native-temp' if step == 'native' else step + '-temp')
    temp.mkdir(exist_ok=step == 'native')
    env['TEMP'] = env['TMP'] = str(temp)
    if step == 'prepare':
        for k in ('LOCALAPPDATA', 'APPDATA'):
            directory = temp / k
            directory.mkdir()
            env[k] = str(directory)
    with (run / (step + '.log')).open('xb') as f:
        # The GUI/Native harness owns its normal close. Never forcibly kill a child on timeout.
        process = subprocess.Popen([c['python'], '-B', '-X', 'utf8', src / 'scripts/release_candidate.py', step, sha, *args],
                                   cwd=src, env=env, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        status = process.wait(timeout=1200)
        require(status == 0, f'{step} failed ({status}); see {f.name}')


def prepare(c, run, sha):
    require(accepted(c, sha), 'Commit not present in locally accepted main')
    run.mkdir()
    state(run, sha, 'building')
    src = run / 'src'
    command([c['git'], '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=NUL', 'clone', '--no-checkout',
             '--no-hardlinks', '--no-tags', '--', c['repo'], str(src)])
    command([c['git'], '-C', str(src), '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=NUL', 'checkout', '--detach', sha])
    candidate(c, run, sha, 'prepare')
    row = identity(run, sha)
    state(run, sha, 'installing', exe_sha256=row['exe_sha256'])
    candidate(c, run, sha, 'install')
    state(run, sha, 'awaiting_native', exe_sha256=row['exe_sha256'], kit_sha256=row['kit_sha256'])


def qualify(c, run, sha, exe_sha):
    row = identity(run, sha)
    require(load(run / 'state.json')['stage'] == 'awaiting_native' and row['exe_sha256'] == exe_sha,
            'A fresh waiting candidate and its exact EXE SHA-256 are required')
    require(main_sha(c) == sha and accepted(c, sha), 'Not current public and locally accepted main')
    # One operation only, including preflight failures. A crash cannot replay it on login.
    state(run, sha, 'native_started', exe_sha256=exe_sha)
    source = Path(c['wrappers'])
    dest = run / 'native-temp' / 'gen_py' / f'{sys.version_info.major}.{sys.version_info.minor}'
    dest.mkdir(parents=True)
    for name in WRAPPERS:
        target = dest / name
        require(digest(source / name) == c['wrapper_hashes'][name], 'Prepared wrapper changed; no cache repair')
        shutil.copyfile(source / name, target)
        require(digest(target) == c['wrapper_hashes'][name], 'Wrapper copy differs')
    candidate(c, run, sha, 'native', '--new-unsigned-toys-for', exe_sha)
    require({p.name: digest(p) for p in dest.iterdir()} == c['wrapper_hashes'], 'Isolated wrapper cache changed')
    candidate(c, run, sha, 'assets', '--upstream', c['upstream'])
    state(run, sha, 'qualified', exe_sha256=exe_sha)


def inventory(run, row):
    parent, v = run / 'public', row['version']
    names = {f'HWPReview-{kind}-{v}.zip' for kind in ('win-x64', 'source', 'corresponding-source')}
    four = names | {'SOURCE-MATERIALS.txt'}
    six = four | {'RELEASE-MANIFEST.json', 'SHA256SUMS.txt'}
    require({p.name for p in parent.iterdir()} == six, 'Unexpected upload set')
    for name in six:
        p = parent / name
        require(p.is_file() and not p.is_symlink() and p.stat().st_nlink == 1 and not p.is_junction(), 'Non-regular public asset')
    hashes = {name: {'sha256': digest(parent / name), 'bytes': (parent / name).stat().st_size} for name in six}
    m = load(parent / 'RELEASE-MANIFEST.json')
    require(m['contract'] == 'hwp-public-release-v1' and m['version'] == v and m['commit'] == row['commit']
            and m['repository'] == URL and m['assets'] == {n: hashes[n] for n in four}
            and m['qualification']['native_evidence_schemas'] == [1, 3, 4]
            and m['executable']['sha256'] == row['exe_sha256']
            and hashes[f'HWPReview-win-x64-{v}.zip']['sha256'] == row['kit_sha256'], 'Staged identity differs')
    sums = ''.join(hashes[n]['sha256'] + '  ' + n + '\n' for n in sorted(six - {'SHA256SUMS.txt'}))
    require((parent / 'SHA256SUMS.txt').read_bytes() == sums.encode(), 'Invalid public checksums')
    return hashes


def check_remote(data, sha, tag, hashes, *, draft):
    require(data['tag_name'] == tag and data['target_commitish'] == sha and data['draft'] is draft
            and data['prerelease'] is True, 'Unexpected remote release identity')
    assets = data['assets']
    require(len(assets) == len(hashes) and all(a['state'] == 'uploaded' for a in assets)
            and {a['name']: (a['digest'], a['size']) for a in assets} ==
            {n: ('sha256:' + r['sha256'], r['bytes']) for n, r in hashes.items()}, 'Remote asset digests differ')


def anonymous(tag, hashes):
    for name, record in hashes.items():
        with urlopen(f'{URL}/releases/download/{tag}/{name}', timeout=90) as response:
            h, count = hashlib.sha256(), 0
            while block := response.read(1024 * 1024):
                count += len(block)
                require(count <= record['bytes'], 'Public download too large')
                h.update(block)
            require(count == record['bytes'] and h.hexdigest() == record['sha256'], 'Anonymous download differs')


def green(c, sha, run_id):
    r = api(c, 'actions/runs/' + str(run_id))
    require(r['head_sha'] == sha and r['head_branch'] == 'main' and r['event'] == 'push'
            and r['path'] == '.github/workflows/check.yml' and r['conclusion'] == 'success'
            and r['head_repository']['full_name'] == REPO, 'Not the required successful public main push')
    jobs = api(c, f'actions/runs/{run_id}/jobs?filter=latest&per_page=100')['jobs']
    require({j['name']: j['conclusion'] for j in jobs} == dict.fromkeys(REQUIRED, 'success'), 'Required CI checks missing or failed')


def publish(c, run, sha, run_id):
    row = identity(run, sha)
    green(c, sha, run_id)
    hashes = inventory(run, row)
    tag = f"v{row['version']}-build.{sha[:12]}"
    require(main_sha(c) == sha and accepted(c, sha), 'Superseded before publication')
    require(not any(r['ref'] == 'refs/tags/' + tag for r in api(c, 'git/matching-refs/tags/' + tag)), 'Tag already exists')
    prior = command([c['gh'], 'release', 'list', '--repo', REPO, '--limit', '1000', '--json', 'tagName'])
    require(tag not in {r['tagName'] for r in json.loads(prior)}, 'Release/draft already exists')
    state(run, sha, 'publishing', tag=tag)
    gh(c, 'api', '--method', 'POST', 'repos/' + REPO + '/git/refs', '-f', 'ref=refs/tags/' + tag, '-f', 'sha=' + sha)
    notes = (f"Windows x64 사전 릴리스 · `{sha}`. 같은 태그에서 Windows 설치 ZIP, 정확한 앱 입력, "
             '대응 소스와 SHA256SUMS.txt를 받으세요. 새 시험 경로에 실제 설치·실행한 ZIP입니다. '
             '한 Windows PC의 새 무서명 합성 문서로 스키마 1·3·4를 검사했습니다. 다른 PC 설치·코드 서명·실제 보고서 검증은 포함하지 않습니다.')
    subprocess.run([c['gh'], 'release', 'create', tag, '--repo', REPO, '--target', sha, '--verify-tag',
                    '--draft', '--prerelease', '--title', 'HWP Editor ' + tag, '--notes', notes,
                    *(str(run / 'public' / n) for n in sorted(hashes))], env=host_env(), stdin=subprocess.DEVNULL,
                   timeout=900, check=True)
    rid = json.loads(gh(c, 'release', 'view', tag, '--repo', REPO, '--json', 'apiUrl'))['apiUrl'].rsplit('/', 1)[-1]
    require(rid.isdigit(), 'Missing immutable release ID')
    endpoint = 'releases/' + rid
    check_remote(api(c, endpoint), sha, tag, hashes, draft=True)
    require(main_sha(c) == sha and api(c, 'git/ref/tags/' + tag)['object']['sha'] == sha, 'Draft tag/main differs')
    gh(c, 'release', 'edit', tag, '--repo', REPO, '--draft=false', '--latest=false')
    check_remote(api(c, endpoint), sha, tag, hashes, draft=False)
    anonymous(tag, hashes)
    state(run, sha, 'published', tag=tag, release_id=rid)
    if main_sha(c) != sha:
        return  # The immutable release exists; a newer revision owns the Pages slot.
    gh(c, 'workflow', 'run', 'docs.yml', '--repo', REPO, '--ref', 'main', '-f', 'tag=' + tag, '-f', 'commit=' + sha)
    state(run, sha, 'pages_requested', tag=tag, release_id=rid)


def observe_pages(c, run, sha, saved):
    tag = saved['tag']
    query = urlencode({'event': 'workflow_dispatch', 'branch': 'main', 'head_sha': sha, 'per_page': 100})
    matches = [r for r in api(c, 'actions/workflows/docs.yml/runs?' + query)['workflow_runs']
               if r['display_title'] == 'Pages ' + tag and r['head_sha'] == sha]
    if not matches or matches[0]['status'] != 'completed':
        return
    require(matches[0]['conclusion'] == 'success', 'Pages did not deploy; previous site retained')
    with urlopen(f'https://mud-the-developer.github.io/hwp-editor-public/release.json?commit={sha}', timeout=30) as r:
        raw = r.read(65537)
    require(len(raw) <= 65536, 'Unexpected Pages metadata size')
    site = json.loads(raw)
    row = identity(run, sha)
    require(site['commit'] == sha and site['tag'] == tag and site['historical'] is False
            and site['assets'] == inventory(run, row) and site['executable']['sha256'] == row['exe_sha256'],
            'Live Pages is not the qualified release')
    state(run, sha, 'deployed', tag=tag, release_id=saved['release_id'], pages_run=matches[0]['id'])


def tick(c, home):
    head = main_sha(c)
    cursor = home / 'since.json'
    query = urlencode({'branch': 'main', 'event': 'push', 'created': '>=' + load(cursor), 'per_page': 100})
    pages = json.loads(gh(c, 'api', '--paginate', '--slurp', f'repos/{REPO}/actions/workflows/check.yml/runs?{query}'))
    rows = sorted((r for p in pages for r in p['workflow_runs']), key=lambda r: (r['created_at'], r['id']))
    for r in rows:
        sha = r['head_sha']
        require(FULL.fullmatch(sha), 'Unexpected public SHA')
        if r['status'] != 'completed' or not accepted(c, sha):
            break  # Never fetch or run a remote-only checkout under the operator account.
        run = home / sha[:12]
        if r['conclusion'] == 'success':
            if not run.exists():
                guarded(run, sha, lambda: prepare(c, run, sha))
            s = load(run / 'state.json')
            require(s['commit'] == sha, 'Local run-key collision')
            if sha == head and s['stage'] == 'qualified':
                guarded(run, sha, lambda: publish(c, run, sha, r['id']))
            elif sha == head and s['stage'] == 'pages_requested':
                guarded(run, sha, lambda: observe_pages(c, run, sha, s))
            elif sha == head and s['stage'] == 'awaiting_native':
                break  # Revisit this SHA after the operator's exact TOY trial.
        save(cursor, r['created_at'])  # Inclusive; local state makes repeated same-second runs harmless.


def guarded(run, sha, operation):
    try:
        operation()
    except Exception:
        if run.is_dir():
            with (run / 'failure.log').open('a', encoding='utf-8') as f:
                traceback.print_exc(file=f)
            failed = load(run / 'state.json') if (run / 'state.json').is_file() else {}
            state(run, sha, 'stopped', failed_stage=failed.get('stage'), tag=failed.get('tag'))
        raise


def enable(args, home):
    repo, upstream, wrappers = (p.resolve(strict=True) for p in (args.repo, args.upstream, args.wrappers))
    c = {'repo': str(repo), 'upstream': str(upstream), 'wrappers': str(wrappers), 'python': sys.executable,
         'wrapper_hashes': {n: digest(wrappers / n) for n in WRAPPERS}}
    for tool in ('gh', 'git', 'dotnet'):
        exe = shutil.which(tool)
        require(exe is not None, 'Missing release tool: ' + tool)
        c[tool] = str(Path(str(exe)).resolve(strict=True))
    head = git(c, 'rev-parse', 'HEAD')
    require(git(c, 'symbolic-ref', '--short', 'HEAD') == 'main' and accepted(c, head)
            and git(c, 'status', '--porcelain', '--untracked-files=normal') == '', 'Enroll clean local public main only')
    main_sha(c)
    require(not home.exists() and not home.is_relative_to(repo), 'New private host directory outside the source required')
    home.mkdir(parents=True)
    save(home / 'config.json', c)
    save(home / 'since.json', now())
    script = home / 'release_host.py'
    shutil.copyfile(Path(__file__), script)
    # The startup shortcut is interactive, non-admin and current-user only. Never overwrite it.
    import win32com.client
    shell = win32com.client.Dispatch('WScript.Shell')
    link = Path(shell.SpecialFolders('Startup')) / 'HWPReview Public Delivery.lnk'
    require(not link.exists(), 'A delivery startup shortcut already exists')
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    require(pythonw.is_file(), 'Windowless Windows Python is required')
    argv = [str(pythonw), '-I', '-B', str(script), 'watch', '--home', str(home)]
    shortcut = shell.CreateShortcut(str(link))
    shortcut.TargetPath, shortcut.Arguments, shortcut.WorkingDirectory = argv[0], subprocess.list2cmdline(argv[1:]), str(home)
    shortcut.Save()
    subprocess.Popen(argv, cwd=home, env=host_env(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    print(f'Registered {link}; local state: {home}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    for name in ('enable', 'watch', 'tick', 'prepare', 'qualify', 'status'):
        p = commands.add_parser(name)
        p.add_argument('--home', type=Path, required=True)
        if name == 'enable':
            for key in ('repo', 'upstream', 'wrappers'):
                p.add_argument('--' + key, type=Path, required=True)
        if name in ('prepare', 'qualify'):
            p.add_argument('--commit', required=True)
        if name == 'qualify':
            p.add_argument('--new-unsigned-toys-for', metavar='EXE_SHA256', required=True)
            p.add_argument('--reviewed-materials', action='store_true', required=True)
    args = parser.parse_args()
    home = args.home.resolve()
    if args.action == 'enable':
        return enable(args, home)
    c = load(home / 'config.json')
    if args.action == 'status':
        print(json.dumps([load(p) for p in sorted(home.glob('*/state.json'))], indent=2)); return
    if args.action == 'watch':
        with lock(home / 'watch.lock') as held:
            if not held:
                return
            while True:
                try:
                    with lock(home / 'operation.lock') as busy:
                        if busy:
                            tick(c, home)
                except Exception as e:
                    save(home / 'host-error.json', {'at': now(), 'error': str(e)})
                time.sleep(180)
    with lock(home / 'operation.lock') as held:
        require(held, 'Another local operation is active; no parallel candidate action')
        if args.action == 'tick':
            return tick(c, home)
        sha = args.commit
        require(FULL.fullmatch(sha), 'Full commit required')
        run = home / sha[:12]
        if args.action == 'prepare':
            require(not run.exists(), 'Existing run: no automatic retry or rebuild')
            guarded(run, sha, lambda: prepare(c, run, sha))
        else:
            require(load(run / 'state.json')['stage'] == 'awaiting_native' and identity(run, sha)['exe_sha256'] == args.new_unsigned_toys_for,
                    'No matching waiting candidate')
            guarded(run, sha, lambda: qualify(c, run, sha, args.new_unsigned_toys_for))
            tick(c, home)


if __name__ == '__main__':
    main()
