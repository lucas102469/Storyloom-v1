// Current comparison flow: no removed requestRevision button or function.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
const begin=code.indexOf('function syncManuscriptPair(){');
const end=code.indexOf('const manuscriptPairView=',begin);
assert.ok(begin>=0&&end>begin,'current comparison function must exist');
function element(){return {hidden:false,dataset:{},textContent:'',classList:{toggle(){},add(){}},
 setAttribute(){},before(){},remove(){},replaceChildren(...values){this.content=values;}};}
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id);};
const main=element(),switches=element(),pane=element();
get('preview').querySelector=selector=>selector==='.draft-switch'?switches:main;
get('documentTab').closest=()=>pane;
const original={text:'original',version:1,status:'confirmed'};
const draft={text:'revised',version:2,status:'draft'};
const originalBody={...element(),scrollTop:50};
let highlights=0;
const context={key:()=> 'episode:2',project:{state:{selected:'episode',episode:2,
 official:{'episode:2':original},drafts:{'episode:2':draft}}},
 $:get,pid:'project',documentView:'preview',editKey:null,viewingOfficial:false,
 manuscriptPair:{project:'project',key:'episode:2'},originalPane:element(),chatPane:element(),
 pairButton:element(),pairNextButton:element(),syncSharedManuscriptScroll(){},originalBody,originalTitle:element(),originalVersion:null,
 documentContent:(text)=>text,window:{manuscriptDiff:{highlight(left,right){
 assert.equal(left,originalBody);assert.equal(right,main);highlights++;}}}};
vm.createContext(context);vm.runInContext(code.slice(begin,end),context);
context.syncManuscriptPair();
assert.equal(highlights,1);assert.equal(main.dataset.sentenceChanges,'true');
assert.equal(context.originalPane.hidden,false);assert.deepEqual(originalBody.content,['original']);
context.syncManuscriptPair();
assert.equal(highlights,1,'same versions must not rebuild existing highlights');
draft.version=3;context.syncManuscriptPair();
assert.equal(highlights,2,'a new draft version must recalculate highlights');
context.manuscriptPair=null;context.syncManuscriptPair();
assert.equal(context.originalPane.hidden,true);assert.equal(main.dataset.sentenceChanges,undefined);
assert.deepEqual(main.content,['revised']);assert.equal(highlights,2);
context.editKey='episode:2';context.syncManuscriptPair();
assert.equal(context.originalPane.hidden,true);
console.log('PASS: comparison highlights only the revised manuscript, restores plain text on close, and stays off during editing');

{
 function dom(){return {children:[],parent:null,scrollTop:99,
  append(...items){for(const item of items){item.remove();item.parent=this;this.children.push(item);}},
  before(item){item.remove();const at=this.parent.children.indexOf(this);this.parent.children.splice(at,0,item);item.parent=this.parent;},
  replaceWith(item){const parent=this.parent,at=parent.children.indexOf(this);item.remove();parent.children[at]=item;item.parent=parent;this.parent=null;},
  remove(){if(this.parent){const at=this.parent.children.indexOf(this);this.parent.children.splice(at,1);this.parent=null;}}};}
 const root=dom(),holder=dom(),preview=dom(),tail=dom(),originalPane=dom(),originalBody=dom();
 holder.append(preview,tail);originalPane.append(originalBody);
 const shared=dom(),left=dom(),right=dom();shared.append(left,right);
 const context2={node:()=>dom(),$:()=>preview,originalBody,originalPane,
  sharedManuscriptScroll:shared,sharedOriginalColumn:left,sharedRevisedColumn:right,
  manuscriptPreviewSlot:null,chatPane:{closest:()=>root}};
 vm.createContext(context2);
 const start=code.indexOf('function syncSharedManuscriptScroll('),end=code.indexOf('const pairNextButton=',start);
 vm.runInContext(code.slice(start,end),context2);
 context2.syncSharedManuscriptScroll(true);
 assert.equal(preview.parent,right);assert.equal(originalBody.parent,left);assert.equal(shared.parent,root);
 shared.scrollTop=123;context2.syncSharedManuscriptScroll(true);assert.equal(shared.scrollTop,123);
 context2.syncSharedManuscriptScroll(false);
 assert.equal(holder.children[0],preview);assert.equal(holder.children[1],tail);
 assert.equal(originalBody.parent,originalPane);assert.equal(shared.parent,null);
 console.log('PASS: both manuscripts share one scroll container and return to their exact original locations on close');
}
