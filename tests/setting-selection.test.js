const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/studio-ui.js'),'utf8');
const original={text:'## 루칸\n설명: 나뭇가지 검객이다.',version:5};
const quote='정보 조직에서 파견된 베테랑 요원이다.';
const editable={...original,key:'characters:0',text:original.text+'\n\n## 밀정\n관계: '+quote};
const body={textContent:'루칸나뭇가지 검객이다.밀정관계'+quote};
const context={editKey:null,documentView:'preview',viewingOfficial:false,
 project:{state:{selected:'characters',drafts:{},official:{'characters:0':original}},setting_edit_source:editable},
 referenceKinds:['characters','world','places','lore'],key:()=> 'characters:0',visibleDocumentDraft:()=>null,
 $:id=>id==='work'?{hidden:false}:id==='preview'?{querySelector:()=>body}:null};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function previewEditableSource('),source.indexOf('function syncPreviewHighlight(')),context);
test('chapter-added setting selection uses the combined editing source',()=>{
 const selected=context.previewEditableSource();
 assert.equal(selected.source,editable);
 const preceding=body.textContent.slice(0,body.textContent.indexOf(quote));
 assert.equal(context.previewSelectionScope(original.text,body.textContent,preceding,quote),null);
 const scope=context.previewSelectionScope(selected.source.text,body.textContent,preceding,quote);
 assert.equal(scope.quote,quote);
 assert.equal(scope.start,Array.from(editable.text.slice(0,editable.text.indexOf(quote))).length);
});
test('historical settings remain read-only',()=>{
 context.viewingOfficial=true;
 assert.equal(context.previewEditableSource(),null);
 context.viewingOfficial=false;
});
test('normal source remains available without combined settings',()=>{
 context.project.setting_edit_source=null;
 assert.equal(context.previewEditableSource().source,original);
});
