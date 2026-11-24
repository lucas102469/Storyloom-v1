const assert = require('node:assert/strict');
const {parsePlotDocument} = require('../static/plot-view.js');
const legacy = '1. 침대 위 일상. 2. 창밖 관찰. 3. 인물의 발견.';
assert.deepEqual(parsePlotDocument(legacy), {overall:'', episodes:'', notes:legacy});
console.log('PASS: scene notes are never presented as a complete plot or episode plan');
const structured = parsePlotDocument('## 전체 플롯\n시작 → 갈등 → 결말\n\n## 회차 구성\n| 회차 | 사건 |\n| --- | --- |\n| 1화 | 발견 |');
assert.equal(structured.overall, '시작 → 갈등 → 결말');
assert.match(structured.episodes, /1화/);
assert.equal(structured.notes, '');
console.log('PASS: full plot and episode plan remain separate');
const {parseSettingRecords,parsePlanDocument}=require('../static/plot-view.js');
const records=parseSettingRecords('주인공: 서윤. 장기 투병 중인 청년. 강하늘: 놀이터에 앉아 있는 청년.');
assert.equal(records.length,2);assert.equal(records[0].name,'서윤');assert.equal(records[1].name,'강하늘');assert.equal(records[0].fields.목표,undefined);
const many=parseSettingRecords(Array.from({length:200},(_,i)=>'## 인물 '+i+'\n성격: 차분함\n목표: 미정').join('\n'));
assert.equal(many.length,200);assert.equal(many[199].fields.성격,'차분함');
const plan=parsePlanDocument('로그라인: 두 사람의 만남\n장르: 판타지\n## 시놉시스\n여정의 시작');assert.equal(plan.fields.장르,'판타지');assert.equal(plan.synopsis,'여정의 시작');
console.log('PASS: structured records preserve all 200 entities and never invent missing facts');
const fixtures=parseSettingRecords('## 강진\n상태: 혈겁 속에서 살아남은 유일한 생존자.\n특징: 태어날 때 주변의 기운을 가라앉힘.\n능력: 별의 기운을 흡수함.\n\n## 미상의 사내\n역할: 마을에 나타난 의문의 인물\n능력: 강진의 생존을 감지함.');
assert.deepEqual(fixtures.map(r=>r.name),['강진','미상의 사내']);assert.match(fixtures[0].fields.상태,/생존자/);assert.match(fixtures[1].fields.능력,/감지/);
const fieldHeadings=parseSettingRecords('## 강진\n### 상태\n살아남은 아이\n### 특징\n붉은 별의 기운\n## 사내\n### 능력\n생존 감지');assert.equal(fieldHeadings.length,2);assert.match(fieldHeadings[0].fields.특징,/붉은 별/);
const tablePeople=parseSettingRecords('| 이름 | 성격 | 목표 |\n| --- | --- | --- |\n| 레온 | 온화함 | 귀환 |\n| 아리아 | 신중함 | 공존 |');assert.deepEqual(tablePeople.map(r=>r.name),['레온','아리아']);
const chapterOnly=parsePlotDocument('## 1화 주요 사건\n- 마을 습격\n- 강진의 생존');assert.equal(chapterOnly.overall,'');assert.equal(chapterOnly.notes,'');assert.match(chapterOnly.episodes,/마을 습격/);
const {parseEpisodeNotes}=require('../static/plot-view.js');assert.equal(parseEpisodeNotes(chapterOnly.episodes)[0].episode,'1');
const wrongPlan=parsePlanDocument('## 전체 플롯\n- 시작: 주인공 탄생\n- 결말: 최후의 선택\n## 회차 구성\n1화: 예언의 시작');assert.equal(wrongPlan.synopsis,'');assert.equal(wrongPlan.notes,'');assert.match(wrongPlan.plot,/예언/);
const markdownPlan=parsePlanDocument('## 로그라인\n별을 찾는 모험\n## 장르\n판타지\n## 시놉시스\n바다로 떠난다.');assert.equal(markdownPlan.fields.로그라인,'별을 찾는 모험');assert.equal(markdownPlan.fields.장르,'판타지');
console.log('PASS: attributes stay on their character; Markdown/table forms work; chapter notes and misplaced plot are classified honestly');

const inlinePlan=parsePlanDocument('로그라인: 낡은 이불의 비밀을 알게 된다. 장르: 현대 판타지, 코미디. ## 전체 플롯 1. 시작: 이불의 정체를 발견한다. ## 회차 구성 1화: 칼날을 막는다.');
assert.equal(inlinePlan.fields.로그라인,'낡은 이불의 비밀을 알게 된다.');
assert.equal(inlinePlan.fields.장르,'현대 판타지, 코미디.');
assert.match(inlinePlan.plot,/## 전체 플롯\n1\. 시작:/);
assert.match(inlinePlan.plot,/## 회차 구성\n1화:/);

const inlineRows=parsePlotDocument('## 전체 플롯\n1. 시작: 발견. 2. 중간: 대립. 3. 결말: 해결.\n## 회차 구성\n1. 1화: 칼날을 막는다.');
assert.deepEqual(inlineRows.overall.split('\n'),['시작: 발견.','중간: 대립.','결말: 해결.']);
assert.equal(inlineRows.episodes,'1화: 칼날을 막는다.');

const legacyOutline='도입부: 소녀가 타인을 물고기로 본다. 전개: 능력의 의미를 탐구한다. 결말: 세상과 화해한다(미확정)';
assert.deepEqual(parsePlotDocument(legacyOutline), {
 overall:'도입부: 소녀가 타인을 물고기로 본다.\n전개: 능력의 의미를 탐구한다.\n결말: 세상과 화해한다(미확정)',
 episodes:'',
 notes:'',
});
const unnamedDescription='사람을 각기 다른 물고기의 모습으로 인식하는 특별한 시력을 가진 소녀(미확정)';
const [unnamed]=parseSettingRecords('주인공: '+unnamedDescription);
assert.equal(unnamed.name,'이름 미정');
assert.equal(unnamed.fields.역할,'주인공');
assert.equal(unnamed.fields.설명,unnamedDescription);
const revisionPlot=parsePlotDocument('## 1화 사건\n\n골목에서 첫 전투를 치른다.');
assert.equal(revisionPlot.notes,'');
assert.equal(revisionPlot.overall,'');
const mixedPlot=parsePlotDocument('제목: 미스터 K, 로그라인: 정체를 숨긴 인물의 이야기, 장르: 미정, 시놉시스: 미정, 전체 플롯: 시작: 미정, 중간: 미정, 결말: 미정, 회차 구성: 1화: 미정, 2화: 단서를 찾는다.');
const inlineHeadings=parsePlotDocument('## 전체 플롯 시작: 가지를 줍는다. 중간: 힘을 얻는다. 결말: 모험을 시작한다. ## 회차 구성 1화: 가지를 발견한다.');
assert.equal(inlineHeadings.overall,'시작: 가지를 줍는다.\n중간: 힘을 얻는다.\n결말: 모험을 시작한다.');
assert.equal(parseEpisodeNotes(inlineHeadings.episodes)[0].description,'가지를 발견한다.');
assert.equal(inlineHeadings.notes,'');
assert.equal(parseSettingRecords('주인공(미정) 역할: 청년 성격: 차분함')[0].fields.성격,'차분함');
assert.equal(mixedPlot.overall,'시작: 미정\n중간: 미정\n결말: 미정');
assert.deepEqual(parseEpisodeNotes(mixedPlot.episodes).map(row=>row.description),['미정','단서를 찾는다.']);
assert.match(mixedPlot.notes,/로그라인/);
assert.doesNotMatch(mixedPlot.notes,/전체 플롯|회차 구성/);
assert.deepEqual(parseEpisodeNotes(revisionPlot.episodes),[
 {episode:'1',title:'',description:'골목에서 첫 전투를 치른다.'},
]);

const futurePlot=parsePlotDocument("## 회차 구성\n1화: 만남\n3화: 갈등을 종결한다.\n\n## 예정 전개\n시점: 나중에\n예정 사건: 소녀가 동료가 된다.");
assert.equal(parseEpisodeNotes(futurePlot.episodes)[1].description,"갈등을 종결한다.");
assert.match(futurePlot.notes,/소녀가 동료/);
