"""Two-company delivery/update/recovery with company-owned native projection proof."""
import copy
import json
from pathlib import Path
from checks import test_system as fixtures
from core import Rejected, config, digest, load, write
import delivery
import history
import hooks
import lifecycle
import operations


class Evolution(fixtures.Fixture):
    def test_company_proofs_survive_delivery_update_rollback_restore(self):
        product, release = fixtures.GitAcceptance.product_release(self)
        a, b = self.base/'alpha', self.base/'beta'
        for root, name in ((a,'alpha-company'),(b,'beta-company')):
            lifecycle.create(release,root,name,'test-owner')
        cfg=config(a);cfg['hooks']={'enabled':True,'disabled':['method-impact']};write(a/'company/config.yaml',cfg)
        foreign={'custom':{'owner':'local'},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'echo preserved local handler','timeout':2}]}]}}
        for harness, relative in hooks.TARGETS.items():
            write(a/relative,foreign)
            self.assertEqual(hooks.project(a,harness,apply=True,enabled=True)['status'],'projected')
        preserved=[*hooks.TARGETS.values(),'.system/hooks-project-codex.json','.system/hooks-project-claude.json']
        self.assertFalse(delivery.git(a,'check-ignore','.codex/hooks.json',check=False).returncode==0)
        delivery.commit(a,['company/config.yaml',*preserved],'Save explicitly allowed fixture projections')
        original={relative:(a/relative).read_bytes() for relative in preserved}
        source='company/sources/design-basis/source.yaml'
        task=operations.intake(a,'scoped-process','Prepare a source-backed note','test-owner',{'kind':'note'},True,inputs=[source],skill='company-execute',harness='codex')
        artifact={'schema_version':1,'company_id':'alpha-company','kind':'note','summary':'Source-backed fixture note','next_action':'Next participant reads the source and note','limitations':['Local fixture only'],'sources':[{'path':source,'sha256':digest(a/source)}]}
        critical={'request_alignment':'Accepted note uses original source','counterexample':'A graph does not replace source bytes','limitations':[],'references':[source]}
        result=operations.execute(a,task['id'],artifact,critical)
        delivery.commit(a,['work/scoped-process/task.json',result['output']['path']],'Verify fixture process')
        remote=self.base/'common.git';delivery.git(a,'clone','--bare',str(a),str(remote))
        delivery.deliver(a,str(remote),task_id=task['id'])
        cfgb=config(b);cfgb['hooks']={'enabled':False,'disabled':['publication-check']};write(b/'company/config.yaml',cfgb)
        (b/'work').mkdir(exist_ok=True);(b/'work/kept.txt').write_text('Company B local work', encoding="utf-8")
        delivery.commit(b,['company/config.yaml','work/kept.txt'],'Save disabled company settings')
        before_a=copy.deepcopy(config(a));before_b=copy.deepcopy(config(b))
        # A canonical method update changes the native fingerprint, not local ownership.
        rule=product/'template/standards/hook-authoring.md';rule.write_text(rule.read_text(encoding="utf-8")+'\nSynthetic release clarification.\n', encoding="utf-8")
        version=load(product/'template/release.yaml');parts=version['version'].split('.');version['version']='.'.join([*parts[:2],str(int(parts[2])+1)]);write(product/'template/release.yaml',version)
        delivery.git(product,'add','template');delivery.git(product,'commit','-m','Fixture hook method release')
        fixtures.product.release(product,release)
        ca,cb=self.base/'alpha-update',self.base/'beta-update'
        self.assertEqual(lifecycle.update(a,release,ca)['status'],'verified-candidate')
        self.assertEqual(lifecycle.update(b,release,cb)['status'],'verified-candidate')
        self.assertEqual(config(ca),before_a);self.assertEqual(config(cb),before_b)
        self.assertEqual((cb/'work/kept.txt').read_text(encoding="utf-8"),'Company B local work')
        for relative, raw in original.items():self.assertEqual((ca/relative).read_bytes(),raw)
        for harness in hooks.TARGETS:
            result=hooks.project(ca,harness,apply=True,enabled=True)
            self.assertEqual(result['status'],'projected',result)
            self.assertIn('unverified',result['native'])
            command=result['projection'][0]['group']['hooks'][0]['command']
            self.assertIn(str(ca),command);self.assertNotIn('--root '+str(a)+' ',command)
            self.assertEqual(load(ca/hooks.TARGETS[harness])['custom'],foreign['custom'])
            self.assertEqual(load(ca/hooks.TARGETS[harness])['hooks']['Stop'][0],foreign['hooks']['Stop'][0])
        self.assertEqual(hooks.dispatch(cb,'codex','SessionStart',{},native=True)['status'],'disabled')
        self.assertEqual(hooks.dispatch(cb,'common','SessionStart',{})['status'],'advisory')
        delivery.commit(ca,preserved,'Reproject fixture paths; native trust still unverified')
        delivery.deliver(ca,str(remote))
        resumed=self.cli('context','--task','scoped-process',root=ca)['tasks'][0]
        self.assertEqual(resumed['status'],'verified');self.assertEqual(resumed['next_action'],artifact['next_action'])
        self.assertEqual(resumed['changed_dependencies'],[])
        latest={relative:(ca/relative).read_bytes() for relative in preserved}
        rollback=self.base/'rolled';self.assertEqual(lifecycle.rollback(ca,rollback)['status'],'verified-candidate')
        for relative,raw in latest.items():self.assertEqual((rollback/relative).read_bytes(),raw)
        self.assertEqual(config(rollback),before_a)
        backup=self.base/'backup';lifecycle.backup(rollback,backup)
        restored=self.base/'restored';lifecycle.restore(backup,restored)
        for relative,raw in latest.items():self.assertEqual((restored/relative).read_bytes(),raw)
        self.assertEqual(self.cli('context','--task','scoped-process',root=restored)['tasks'][0]['status'],'verified')
        for harness in hooks.TARGETS:
            result=hooks.project(restored,harness,apply=True,enabled=True)
            self.assertEqual(result['status'],'projected',result)
            self.assertIn(str(restored),result['projection'][0]['group']['hooks'][0]['command'])
            self.assertIn('unverified',result['native'])
        # Exact proof paths are not silently admitted by generic company export.
        files=[p for p in delivery.git(ca,'ls-files','-z').split('\x00') if p and 'hooks-project-' not in p]
        policy=history.Policy('company',files=tuple(files),projections=True)
        with self.assertRaisesRegex(Rejected,'export policy excludes'):
            history.scan(ca,policy)
        package=self.base/'forbidden-proposal';package.mkdir()
        write(package/'approval.json',{'approved_by':'test-owner','permission':'share-sanitized-method','purpose':'Must not share company ownership proof','files':{'.system/hooks-project-codex.json':'0'*64}})
        with self.assertRaisesRegex(Rejected,'allowlist'):lifecycle.proposal(ca,package,self.base/'rejected-proposal')
        self.assertFalse((ca/'docs/development').exists())

    def test_local_hook_proposal_has_exact_approval_and_reviewable_replacement(self):
        product,release=fixtures.GitAcceptance.product_release(self)
        company=self.base/'local-hooks-company';lifecycle.create(release,company,'local-hooks-client','test-owner')
        declaration=load(company/'hooks/manifest.yaml')
        declaration['hooks'][2]['purpose']='Check new method map links after the write'
        write(company/'hooks/manifest.yaml',declaration)
        code=(company/'scripts/hooks.py').read_text(encoding="utf-8")+'\n# General method improvement: retain shared validator ownership.\n'
        (company/'scripts/hooks.py').write_text(code, encoding="utf-8")
        self.assertEqual(hooks.manifest(company)['hooks'][2]['purpose'],declaration['hooks'][2]['purpose'])
        package=self.base/'authored-sanitized-hook';package.mkdir()
        authored={'hooks/manifest.yaml':(company/'hooks/manifest.yaml').read_bytes(),'scripts/hooks.py':code.encode(),
                  'checks/test_hook_proposal.py':b'"""Proposed local regression; maintainer runs after review."""\nimport unittest\nclass HookProposal(unittest.TestCase):\n    def test_method_map(self):\n        from pathlib import Path\n        from hooks import manifest\n        item=next(x for x in manifest(Path(__file__).resolve().parents[1])["hooks"] if x["id"]=="entity-after-write")\n        self.assertEqual(item["events"],["PostToolUse"])\n'}
        for relative,raw in authored.items():
            file=package/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(raw)
        write(package/'approval.json',{'approved_by':'test-owner','permission':'share-sanitized-method','purpose':'Share generalized hook/map improvement',
              'files':{relative:digest(package/relative) for relative in authored}})
        remote=self.base/'general-product.git';delivery.git(product,'clone','--bare',str(product),str(remote))
        before={relative:(product/'template'/relative).read_bytes() for relative in ['hooks/manifest.yaml','scripts/hooks.py']}
        candidate=self.base/'proposed-product'
        result=lifecycle.proposal_candidate(company,package,product,str(remote),candidate)
        self.assertEqual(result['status'],'reviewable-product-candidate')
        self.assertEqual(set(result['replacements']),{'template/hooks/manifest.yaml','template/scripts/hooks.py'})
        self.assertIn('donated code was not executed',result['acceptance'])
        for relative,raw in authored.items():self.assertEqual((candidate/'template'/relative).read_bytes(),raw)
        for relative,raw in before.items():self.assertEqual((product/'template'/relative).read_bytes(),raw)
        self.assertTrue(delivery.git(candidate,'status','--porcelain'))
        self.assertEqual(delivery.git(candidate,'rev-parse','HEAD'),delivery.git(product,'rev-parse','HEAD'))
        self.assertNotIn(company.name,delivery.git(candidate,'log','--format=%s'))
        self.assertFalse((candidate/'template/.system/hooks-project-codex.json').exists())
        # The structural preparer must never import unreviewed donated code.
        poison=package/'scripts/hooks.py';poison.write_text('raise RuntimeError("Unreviewed donated code executed")\n'+code, encoding="utf-8")
        approval=load(package/'approval.json');approval['files']['scripts/hooks.py']=digest(poison);write(package/'approval.json',approval)
        inert=lifecycle.proposal_candidate(company,package,product,str(remote),self.base/'inert-product-candidate')
        self.assertEqual(inert['status'],'reviewable-product-candidate')
        self.assertIn('maintainer review',inert['acceptance'])

    def test_hook_proposal_rejects_bad_approval_data_and_nonfixed_script(self):
        package=self.base/'hook-negative';package.mkdir()
        examples=['scripts/arbitrary.py','.claude/settings.json','.codex/hooks.json','.system/hooks-project-codex.json','company/README.md']
        for n,relative in enumerate(examples):
            file=package/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('Synthetic candidate', encoding="utf-8")
            write(package/'approval.json',{'approved_by':'test-owner','permission':'share-sanitized-method','purpose':'Negative path', 'files':{relative:digest(file)}})
            with self.assertRaisesRegex(Rejected,'allowlist'):lifecycle.proposal(self.root,package,self.base/f'negative-{n}')
        file=package/'scripts/hooks.py';file.write_text('print("test-company")\n', encoding="utf-8")
        write(package/'approval.json',{'approved_by':'test-owner','permission':'share-sanitized-method','purpose':'Negative identity', 'files':{'scripts/hooks.py':digest(file)}})
        with self.assertRaisesRegex(Rejected,'private identifiers'):lifecycle.proposal(self.root,package,self.base/'negative-identity')
