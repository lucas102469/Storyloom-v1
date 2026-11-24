const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element{
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.hidden=false;this.isConnected=false;}
 append(...items){this.children.push(...items);for(const item of items)item.isConnected=true;}
 replaceChildren(...items){this.text='';this.children=items;}
 setAttribute(){}
 after(item){item.isConnected=true;}
 click(){}
 get textContent(){return this.text+this.children.map(x=>x.textContent||'').join('');}
}
const source=fs.readFileSync(path.join(__dirname,'../static/setting-history.js'),'utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function setup(available=true){
 const nodes=new Map(),calls=[];
 const ctx={pid:'p',project:{state:{selected:'characters',official:{'characters:0':{version:9,text:'미래 설정'}},drafts:{},records:{}}},
  referenceKinds:['characters','world','places','lore'],documentView:'preview',viewingOfficial:false,
  node:(tag,text)=>new Element(tag,text),$:id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);},
  safeMove(){},notice(message){throw Error(message);},setDocumentView(){},
  reflectedSettingFacts:state=>{assert.equal(state.records['1'].findings[0].fact,'당시 사실');return [];},
  documentContent:text=>{
   assert.equal(ctx.project.state.official['characters:0'].text,'당시 인물');
   return new Element('p',text);
  },api:async url=>{
   calls.push(url);
   return url.includes('?')?{available,official:{'characters:0':{text:'당시 인물'},'world:0':{text:'당시 세계'}},
    records:{1:{findings:[{fact:'당시 사실'}]}}}:{options:[{episode:0,label:'초기 설정'},{episode:1,label:'1화 설정'}]};
  }};
 ctx.preview=()=>{ctx.viewingOfficial=false;ctx.$('editBtn').hidden=false;ctx.$('preview').replaceChildren(new Element('p','현재 화면'));};
 ctx.render=()=>ctx.preview();ctx.$('documentTab').parentElement=new Element('nav');
 vm.createContext(ctx);vm.runInContext(source,ctx);
 return {ctx,calls,nodes,selector:()=>ctx.$('referenceTabs').children.at(-1)};
}
test('historical tabs show snapshot data read-only and restore the current project',async()=>{
 const env=setup(),ctx=env.ctx;
 ctx.render();await flush();await flush();
 const select=env.selector().children[0],current=ctx.project;
 assert.deepEqual(select.children.map(el=>el.text),['현재 설정','초기 설정','1화 설정']);
 select.value='1';await select.onchange();
 assert.match(ctx.$('preview').textContent,/당시 인물/);
 assert.doesNotMatch(ctx.$('preview').textContent,/미래 설정/);
 assert.equal(ctx.$('editBtn').hidden,true);assert.equal(ctx.project,current);
 const count=env.calls.length;
 ctx.project.state.selected='world';ctx.render();
 assert.match(ctx.$('preview').textContent,/당시 세계/);assert.equal(env.calls.length,count);
 select.value='current';await select.onchange();
 assert.equal(ctx.$('preview').textContent,'현재 화면');assert.equal(ctx.$('editBtn').hidden,false);
});
test('missing history is explicit and never falls back to current settings',async()=>{
 const env=setup(false),{ctx}=env;
 ctx.render();await flush();await flush();
 env.selector().children[0].value='1';await env.selector().children[0].onchange();
 assert.match(ctx.$('preview').textContent,/당시 설정 기록이 없어요/);
 assert.doesNotMatch(ctx.$('preview').textContent,/미래 설정/);
});

test('new settings return to current, but chapter drafting and tab changes keep the chosen history',async()=>{
 const env=setup(),{ctx}=env;
 ctx.render();await flush();await flush();
 const select=env.selector().children[0];select.value='1';await select.onchange();
 ctx.project.state.official['episode:2']={version:20,text:'새 원고'};
 ctx.project.state.selected='world';ctx.render();await flush();await flush();
 assert.equal(select.value,'1');assert.match(ctx.$('preview').textContent,/당시 세계/);
 ctx.project.state.records['2']={version:20,setting_review:'done',findings:[]};
 ctx.render();await flush();await flush();assert.equal(select.value,'1');
 ctx.project.state.records['2'].findings=[{kind:'characters',name:'새 인물',fact:'중요한 새 설정'}];
 ctx.render();await flush();await flush();
 assert.equal(select.value,'current');assert.equal(ctx.$('preview').textContent,'현재 화면');
 select.value='1';await select.onchange();
 ctx.project.state.drafts['characters:0']={version:22,text:'추가 설정 수정안'};
 ctx.render();await flush();await flush();assert.equal(select.value,'current');
});

test('selection and latest-setting detection survive reloading the presentation script',async()=>{
 const env=setup(),{ctx}=env;const saved=new Map();
 ctx.localStorage={getItem:key=>saved.get(key)||null,setItem:(key,value)=>saved.set(key,value)};
 ctx.render();await flush();await flush();
 env.selector().children[0].value='1';await env.selector().children[0].onchange();
 const stored=JSON.parse(saved.get('storyloom-setting-view:p'));assert.equal(stored.choice,'1');
 const restored=setup();restored.ctx.localStorage=ctx.localStorage;
 restored.ctx.render();await flush();await flush();
 assert.equal(restored.selector().children[0].value,'1');
 restored.ctx.project.state.official['characters:0'].version=99;
 restored.ctx.render();await flush();await flush();
 assert.equal(restored.selector().children[0].value,'current');
});
