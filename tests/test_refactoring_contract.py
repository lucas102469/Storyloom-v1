"""Structural and runtime compatibility against the read-only pre-refactor snapshot."""
import ast
from contextlib import closing
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from app import create_app

ROOT=Path(__file__).resolve().parents[1]
BASELINE=ROOT/'refactoring_verification/app_before.py.txt'

class OriginalNames(ast.NodeTransformer):
    def visit_Attribute(self,node):
        if isinstance(node.value,ast.Attribute) and isinstance(node.value.value,ast.Name) and node.value.value.id=='ctx' and node.value.attr=='compat':
            return ast.copy_location(ast.Name(id=node.attr,ctx=node.ctx),node)
        if isinstance(node.value,ast.Name) and node.value.id=='ctx':
            return ast.copy_location(ast.Name(id=node.attr,ctx=node.ctx),node)
        return self.generic_visit(node)

@unittest.skipUnless(BASELINE.exists(), "Local pre-refactor verification artifacts are not distributed")
class RefactoringContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'refactoring_verification')
        self.apps=[]
    def tearDown(self):
        for app in self.apps:app.extensions['storyloom_workers'].shutdown(wait=True)
        self.temp.cleanup()
    def app(self,factory=create_app,name='current'):
        app=factory(str(Path(self.temp.name)/(name+'.sqlite3')),lambda _: {'reply':'ok','updates':[]})
        self.apps.append(app);return app
    def baseline_factory(self):
        name='storyloom_pre_refactor_contract'
        loader=importlib.machinery.SourceFileLoader(name,str(BASELINE))
        spec=importlib.util.spec_from_loader(name,loader)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;loader.exec_module(module)
        return module.create_app
    def originals(self):
        tree=ast.parse(BASELINE.read_text(encoding='utf-8'))
        factory=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='create_app')
        return factory,{n.name:n for n in factory.body if isinstance(n,ast.FunctionDef)}
    def test_moved_functions_preserve_exact_syntax_beyond_context_bindings(self):
        _,before=self.originals();after={}
        entry=ast.parse((ROOT/'app.py').read_text(encoding='utf-8'))
        factory=next(n for n in entry.body if isinstance(n,ast.FunctionDef) and n.name=='create_app')
        after.update({n.name:n for n in factory.body if isinstance(n,ast.FunctionDef)})
        for path in (ROOT/'storyloom').glob('*.py'):
            source=path.read_text(encoding='utf-8')
            if path.name=='security.py':
                source=source.replace("if request.host.split(':')[0].lower() not in ctx.app.config['STORYLOOM_ALLOWED_HOSTS']:", "if request.host.split(':')[0] not in ('127.0.0.1', 'localhost'):")
            tree=ast.parse(source)
            install=next((n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install'),None)
            if install:after.update({n.name:n for n in install.body if isinstance(n,ast.FunctionDef)})
        self.assertEqual(set(before),set(after))
        for name in before:
            with self.subTest(function=name):
                normalized=OriginalNames().visit(after[name])
                self.assertEqual(ast.dump(before[name],include_attributes=False),ast.dump(normalized,include_attributes=False))
    def test_schema_initializer_is_unchanged(self):
        factory,_=self.originals()
        before=next(n for n in factory.body if isinstance(n,ast.With))
        tree=ast.parse((ROOT/'storyloom/database.py').read_text(encoding='utf-8'))
        install=next(n for n in tree.body if isinstance(n,ast.FunctionDef))
        after=next(n for n in install.body if isinstance(n,ast.With))
        self.assertEqual(ast.dump(before,include_attributes=False),ast.dump(OriginalNames().visit(after),include_attributes=False))
    def test_all_routes_methods_errors_and_basic_responses_match(self):
        before=self.app(self.baseline_factory(),'baseline');after=self.app()
        def routes(app):return sorted((r.rule,r.endpoint,tuple(sorted(r.methods)),r.strict_slashes) for r in app.url_map.iter_rules())
        self.assertEqual(routes(before),routes(after))
        for method,path,kwargs in [('get','/api/auth/session',{}),('get','/api/usage',{}),('get','/api/projects',{}),
                ('get','/api/projects/missing',{}),('get','/',{'base_url':'http://untrusted.example'}),
                ('post','/api/projects',{'json':{},'headers':{'Origin':'http://untrusted.example'}})]:
            with self.subTest(method=method,path=path):
                a=getattr(before.test_client(),method)(path,**kwargs);b=getattr(after.test_client(),method)(path,**kwargs)
                self.assertEqual((a.status_code,a.json),(b.status_code,b.json))
        with closing(sqlite3.connect(Path(self.temp.name)/'baseline.sqlite3')) as a,closing(sqlite3.connect(Path(self.temp.name)/'current.sqlite3')) as b:
            sql='SELECT name,sql FROM sqlite_master ORDER BY name'
            self.assertEqual(a.execute(sql).fetchall(),b.execute(sql).fetchall())
    def test_multiple_app_instances_keep_state_and_workers_isolated(self):
        a=self.app(name='one');b=self.app(name='two')
        ca,cb=a.test_client(),b.test_client()
        pid=ca.post('/api/projects',json={'title':'only one'}).json['id']
        self.assertEqual(cb.get('/api/projects').json,[])
        self.assertNotEqual(ca.get('/api/projects').json,[])
        self.assertIsNot(a.extensions['storyloom_workers'],b.extensions['storyloom_workers'])
        self.assertEqual(ca.post('/api/projects/'+pid+'/chat',json={'request_id':'isolation-request-001','text':'hello'}).status_code,200)
        self.assertEqual(ca.get('/api/usage').json['used'],1)
        self.assertEqual(cb.get('/api/usage').json['used'],0)
    def test_default_db_template_and_static_paths_stay_inside_copy(self):
        real_connect=sqlite3.connect;seen=[]
        def connect(path,*args,**kwargs):
            seen.append(Path(path).resolve())
            self.assertEqual(Path(path).resolve(),(ROOT/'storyloom.sqlite3').resolve())
            return real_connect(Path(self.temp.name)/'redirected.sqlite3',*args,**kwargs)
        with patch('app.sqlite3.connect',side_effect=connect):
            app=create_app(ai=lambda _: {})
            self.apps.append(app)
        self.assertTrue(seen)
        self.assertEqual(Path(app.root_path).resolve(),ROOT)
        response=app.test_client().get('/')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Storyloom',response.data)
    def test_existing_modules_ui_and_tests_are_byte_identical(self):
        manifest=json.loads((ROOT/'refactoring_verification/source_manifest.json').read_text(encoding='utf-8'))
        for name,digest in manifest.items():
            if name=='app.py':continue
            with self.subTest(path=name):
                if name in ('README.md','.gitignore'):
                    old=(ROOT/('refactoring_verification/deployment_before_'+name.replace('.','_'))).read_bytes()
                    self.assertEqual(hashlib.sha256(old).hexdigest(),digest)
                    hashes=json.loads((ROOT/'refactoring_verification/deployment_document_hashes.json').read_text(encoding='utf-8'))
                    self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),hashes[name])
                elif Path(name).as_posix()=='requirements.txt':
                    expected=(ROOT/name).read_text(encoding='utf-8').replace('google-genai>=2.34,<3\nhttpx>=0.28,<1\ngunicorn>=23,<24','google-genai>=2.34,<3')
                    baseline=(ROOT/'refactoring_verification/deployment_requirements_before.txt').read_bytes()
                    self.assertEqual(hashlib.sha256(baseline).hexdigest(),digest)
                    self.assertEqual(expected,baseline.decode('utf-8').replace('\r\n','\n'))
                elif Path(name).as_posix()=='static/studio-ui.js':
                    before=(ROOT/'refactoring_verification/studio_ui_before_length_fix.js.txt').read_bytes()
                    self.assertEqual(hashlib.sha256(before).hexdigest(),digest)
                    removed=b"if(!recommendation)recommendationPanel.querySelector('button')?.click();"
                    lines=before.splitlines(keepends=True)
                    self.assertEqual(sum(line.strip()==removed for line in lines),1)
                    self.assertEqual((ROOT/name).read_bytes(),b''.join(line for line in lines if line.strip()!=removed))
                elif Path(name).as_posix() in ('static/studio-preview.js','tests/setting_card_render.test.js'):
                    fixes=json.loads((ROOT/'refactoring_verification/authorized_ui_fixes.json').read_text(encoding='utf-8'))
                    fix=fixes[Path(name).as_posix()]
                    expected=(ROOT/fix['snapshot']).read_bytes()
                    self.assertEqual(hashlib.sha256(expected).hexdigest(),digest)
                    for pair in fix['replacements']:
                        old,new=pair['old'].encode('utf-8'),pair['new'].encode('utf-8')
                        self.assertEqual(expected.count(old),1)
                        expected=expected.replace(old,new)
                    self.assertEqual((ROOT/name).read_bytes(),expected)
                elif Path(name).as_posix()=='story_additions.py':
                    fix=json.loads((ROOT/'refactoring_verification/authorized_appearance_fix.json').read_text(encoding='utf-8'))
                    expected=(ROOT/fix['snapshot']).read_bytes()
                    self.assertEqual(hashlib.sha256(expected).hexdigest(),digest)
                    for pair in fix['replacements']:
                        old,new=pair['old'].encode('utf-8'),pair['new'].encode('utf-8')
                        self.assertEqual(expected.count(old),1)
                        expected=expected.replace(old,new)
                    self.assertEqual((ROOT/name).read_bytes(),expected)
                else:
                    self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest)

if __name__=='__main__':unittest.main()
