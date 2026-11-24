const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {parseSettingRecords}=require('../static/plot-view.js');
class Element {
 constructor(tag,text=''){this.tag=tag;this.text=text;this.children=[];this.style={};this.className='';this.classList={add(){}};}
 append(...items){this.children.push(...items);}
 replaceChildren(...items){this.text='';this.children=items;}
 get childNodes(){return this.children;}
 get childElementCount(){return this.children.length;}
 get lastChild(){return this.children.at(-1);}
 get textContent(){return this.text+this.children.map(item=>item.textContent??String(item)).join('');}
}
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
const context={parseSettingRecords,project:{state:{official:{}}},referenceKinds:['characters','world','places','lore'],
 node:(tag,text)=>new Element(tag,text),richText:text=>new Element('div',text),svgIcon:()=>new Element('svg')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function reflectedFactsForDocument('),source.indexOf('function planDocument(')),context);
function titles(root){
 const found=[];
 function visit(item){if(item.className==='setting-entity-title')found.push(item.textContent);for(const child of item.children||[])visit(child);}
 visit(root);return found;
}
test('world heading owns colon attributes including unfamiliar labels',()=>{
 const text='## 신아\n설명: 신아는 금융 등급으로 신분이 정해지는 도시다.\n도시의 핵심 규칙: 돈이 곧 법이다. 검은 거래가 도시를 지배한다.';
 const result=context.documentContent(text,'world','confirmed');
 assert.deepEqual(titles(result),['신아']);
 assert.match(result.children[0].textContent,/신아는 금융 등급/);
 assert.match(result.children[0].textContent,/돈이 곧 법이다/);
 assert.equal(result.children.length,1);
});
test('unlisted attributes never become characters or settings and saved information stays visible',()=>{
 for(const kind of ['characters','world','places','lore']){
  const text='## 한유진\n설명: 잠을 원하는 인물.\n직업: 회사원\n특기: 빠르게 잠들기\n새항목명: 독립된 인물이 아니다.\n\n## 강팀장\n설명: 상사.';
  const rendered=context.documentContent(text,kind,'draft');
  assert.deepEqual(titles(rendered),['한유진','강팀장']);
  assert.match(rendered.textContent,/빠르게 잠들기/);
  assert.match(rendered.textContent,/독립된 인물이 아니다/);
  assert.match(rendered.textContent,/회사원/);
 }
});
test('generic environment and danger sections stay inside the owning setting automatically',()=>{
 for(const [kind,label] of [['world','환경'],['places','위험도'],['lore','등급']]){
  for(const suffix of [label+': 최고 등급','## '+label+'\n설명: 최고 등급']){
   const rendered=context.documentContent('## 투기장\n설명: 거대한 장벽의 공간.\n'+suffix,kind,'draft');
   assert.deepEqual(titles(rendered),['투기장']);
   assert.match(rendered.textContent,/최고 등급/);
  }
 }
});
test('weapon stays an attribute of its character and following traits stay with it',()=>{
 const text='## 진소한\n역할: 주인공\n설명: 뛰어난 검사의 이야기.\n무기: 보검\n특징: 핏빛 매혹과 고독에 깊이 취해 있음';
 const records=parseSettingRecords(text);
 assert.equal(records.length,1);
 assert.equal(records[0].fields.무기,'보검');
 assert.equal(records[0].fields.특징,'핏빛 매혹과 고독에 깊이 취해 있음');
 const rendered=context.documentContent(text,'characters','draft');
 assert.deepEqual(titles(rendered),['진소한']);
 assert.match(rendered.textContent,/무기보검/);
});
test('character overview keeps introduction personality goal and relationship while extras are collapsed',()=>{
 const text='## 진소한\n역할: 주인공\n설명: 뛰어난 검사.\n성격: 냉철함\n목표: 운명을 찾는다.\n관계: 동료를 신뢰한다.\n무기: 보검';
 const root=context.documentContent(text,'characters','confirmed');
 const entity=root.children[0].children.at(-1);
 const overview=entity.children.filter(item=>item.tag!=='details').map(item=>item.textContent).join('');
 assert.match(overview,/뛰어난 검사/);
 assert.match(overview,/목표운명을 찾는다/);
 assert.match(overview,/관계동료를 신뢰한다/);
 assert.match(overview,/성격냉철함/);
 assert.doesNotMatch(overview,/보검/);
 const details=entity.children.find(item=>item.tag==='details');
 assert.ok(details);
 assert.equal(details.open,undefined);
 assert.equal(details.children[0].textContent,'상세 보기');
 assert.doesNotMatch(details.textContent,/성격냉철함/);
 assert.match(details.textContent,/무기보검/);
 assert.doesNotMatch(details.textContent,/목표|관계|설명|뛰어난 검사|운명을 찾는다|동료를 신뢰한다/);
});
test('separate world headings and older inline topic syntax remain separate cards',()=>{
 assert.deepEqual(titles(context.documentContent('## 신아\n규칙: 자본이 지배한다.\n## 바다\n설명: 배가 다닌다.','world','confirmed')),['신아','바다']);
 assert.deepEqual(titles(context.documentContent('신아: 자본이 지배한다.\n바다: 배가 다닌다.','world','confirmed')),['신아','바다']);
});
test('actual draft rendering merges renamed facts without adding the old card',()=>{
 const original='## 강진혁\n역할: 주인공\n목표: 정상에 오른다.';
 context.project.state.official={'characters:0':{text:original}};
 const reflected=[{name:'강진혁',fact:'비자금 데이터를 확보했다.',role:'주인공',field:'목표'}];
 const draft=context.documentContent(original.replace('강진혁','지진라'),'characters','draft',reflected);
 assert.deepEqual(titles(draft),['지진라']);
 assert.match(draft.textContent,/비자금 데이터를 확보했다/);
 assert.deepEqual(titles(context.documentContent(original,'characters','confirmed',reflected)),['강진혁']);
 assert.equal(reflected[0].name,'강진혁');
});

test('edited chapter settings do not reattach the superseded fact',()=>{
 const oldFact='정보 조직에서 파견된 베테랑 요원이다.';
 const text='## 밀정\n역할: 정보 요원\n관계: 왕실 소속의 베테랑 요원이다.';
 context.project.state={official:{'characters:0':{text,reference_materialized_facts:[oldFact]}},drafts:{}};
 const result=context.documentContent(text,'characters','confirmed',[{name:'밀정',fact:oldFact,field:'관계'}]);
 assert.match(result.textContent,/왕실 소속/);
 assert.ok(!result.textContent.includes(oldFact));
});
