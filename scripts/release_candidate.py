"""Private per-commit build/install and explicitly authorized synthetic Native operations."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import build_exe
import prepare_public_release as public
from hwp_release_sources import PUBLIC_REPOSITORY, stage_public_assets

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def record(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2)


def prepare(run, commit):
    result = build_exe.build()
    candidate = ROOT / result['candidate_dir']
    manifest = public.prepare(candidate, run / 'distribution', commit, PUBLIC_REPOSITORY)
    version = manifest['version']
    kit = f'HWPReview-win-x64-{version}.zip'
    identity = {'commit': commit, 'version': version, 'candidate': result['candidate_dir'],
                'kit_sha256': manifest['artifacts'][kit]['sha256'], 'exe_sha256': result['sha256']}
    record(run / 'built.json', identity)


def install(run, identity):
    from release_install_smoke import run as smoke

    kit = run / 'distribution/installer' / f"HWPReview-win-x64-{identity['version']}.zip"
    bindings = {key: identity[key] for key in ('kit_sha256', 'exe_sha256')}
    result = smoke(kit, run / 'smoke', **bindings)
    if any(result.get(k) != v for k, v in bindings.items()):
        raise ValueError('Installed Windows smoke result differs from built transport')


def evidence(run):
    base = ROOT / 'work'
    return (base / ('toy-text-apply-' + run.name) / 'TOY-ONLY-text-apply-verification.json',
            *(base / f'toy-contract-{n}-{run.name}' / 'TOY-ONLY-contract-verification.json' for n in (3, 4)))


def native(run, identity, consent_digest):
    # This entry point is never dispatched by the unattended host. The caller must
    # name the exact digest and authorize fresh unsigned TOYs in this invocation.
    if consent_digest != identity['exe_sha256']:
        raise ValueError('Exact SHA-256 required for this trial')
    exe = ROOT / identity['candidate'] / 'HWPReview.exe'
    if digest(exe) != consent_digest:
        raise ValueError('Exact executable no longer present')
    sys.path.insert(0, str(ROOT / 'tests'))
    import native_text_apply_smoke as text
    import native_contract_smoke as fixed
    from native_blank_metadata_probe import hwp_inventory
    from hwp_security import automation_cache, registered_security_module

    if Path(os.environ['TEMP']).resolve() != (run / 'native-temp').resolve():
        raise ValueError('Private prepared Native temp required')
    registered_security_module()
    with automation_cache():
        pass  # Verifies existing typed wrappers; does not create a Hancom session.
    text.qualification_preflight(exe)
    for schema in (3, 4):
        fixed.qualification_preflight(exe, schema)
    labs = evidence(run)
    (ROOT / 'work').mkdir(exist_ok=True)
    for schema, proof in zip((1, 3, 4), labs, strict=True):
        if hwp_inventory() != [] or proof.parent.exists():
            raise RuntimeError('Unknown Hancom ownership or reused Native lab')
        if schema == 1:
            result = text.run(proof.parent, exe, run_native=True, toy_only=True)
        else:
            result = fixed.run(proof.parent, exe, schema, run_native=True, toy_only=True)
        if (result.get('status') != 'checks_passed_needs_human_visual_review'
                or result.get('executable_sha256') != consent_digest or hwp_inventory() != []):
            raise RuntimeError(f'Native schema {schema} failed or cleanup uncertain')
    build_exe.validate_checked_candidate(exe.parent, *labs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=('prepare', 'install', 'native', 'assets'))
    parser.add_argument('commit')
    parser.add_argument('--new-unsigned-toys-for', metavar='EXE_SHA256')
    parser.add_argument('--upstream', type=Path)
    args = parser.parse_args()
    run = ROOT.parent
    if (ROOT.name != 'src' or run.name != args.commit[:12]
            or public.git('rev-parse', 'HEAD').decode().strip() != args.commit):
        parser.error('Expected the exact private per-commit checkout')
    if args.step == 'prepare':
        return prepare(run, args.commit)
    identity = json.loads((run / 'built.json').read_bytes())
    if identity['commit'] != args.commit:
        raise ValueError('Candidate commit changed')
    if args.step == 'install':
        return install(run, identity)
    receipt = json.loads((run / 'smoke/installed-smoke.json').read_bytes())
    if any(receipt.get(k) != identity[k] for k in ('kit_sha256', 'exe_sha256')):
        raise ValueError('Missing installed artifact evidence')
    if args.step == 'native':
        return native(run, identity, args.new_unsigned_toys_for)
    one, three, four = evidence(run)
    stage_public_assets(ROOT / identity['candidate'], run / 'distribution', args.upstream,
                        run / 'public', commit=args.commit, kit_sha256=identity['kit_sha256'],
                        exe_sha256=identity['exe_sha256'], native_evidence=one,
                        layout_evidence=three, fill_evidence=four)


if __name__ == '__main__':
    main()
