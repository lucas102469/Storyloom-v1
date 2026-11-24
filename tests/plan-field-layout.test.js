const {test}=require('node:test'),assert=require('node:assert/strict');
const {parsePlanDocument}=require('../static/plot-view.js');

test('planning values on following lines populate fields instead of planning notes',()=>{
 const original='제목:\n꿈의 잔해\n로그라인:\n꿈을 포기한 사람이 과거의 열망과 마주하는 심리 드라마.\n장르:\n드라마, 미스터리\n\n## 시놉시스\n낡은 물건을 발견하고 잊었던 꿈을 돌아본다.';
 const result=parsePlanDocument(original);
 assert.equal(result.title,'꿈의 잔해');
 assert.equal(result.fields.로그라인,'꿈을 포기한 사람이 과거의 열망과 마주하는 심리 드라마.');
 assert.equal(result.fields.장르,'드라마, 미스터리');
 assert.equal(result.synopsis,'낡은 물건을 발견하고 잊었던 꿈을 돌아본다.');
 assert.equal(result.notes,'');
});
test('mixed layouts aliases markdown and empty fields preserve the next field',()=>{
 const result=parsePlanDocument('**가제：**\r\n꿈의 잔해\r\n로그라인 : 기억을 찾는다.\r\n장르:\r\n미스터리\r\n## 시놉시스\r\n과거와 마주한다.');
 assert.equal(result.title,'꿈의 잔해');assert.equal(result.fields.로그라인,'기억을 찾는다.');
 assert.equal(result.fields.장르,'미스터리');assert.equal(result.notes,'');
 const missing=parsePlanDocument('제목:\n로그라인:\n기억을 찾는다.\n장르: 미스터리\n## 시놉시스\n과거와 마주한다.');
 assert.equal(missing.title,'');assert.equal(missing.fields.로그라인,'기억을 찾는다.');
 assert.equal(missing.fields.장르,'미스터리');
});
