const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const Calculator=vm.runInNewContext(fs.readFileSync('lorasim/static/experience.js','utf8')+'\nExperienceCalculator');

test('experience map preserves metric aspect ratio and includes empty/outside bins',()=>{
 const svg=Calculator.mapSVG([5,0,0,5,2],[2000,1000],2,12);
 assert.ok(svg.includes('viewBox="0 0 400 200"'));
 assert.ok(svg.includes('preserveAspectRatio="xMidYMid meet"'));
 assert.equal((svg.match(/<title>/g)||[]).length,4);
 assert.ok(svg.includes('5 / 12 (41.7%)'));
});
test('timeline stacks states as counts and handles no transmissions',()=>{
 const svg=Calculator.timelineSVG([[100,0,0],[0,100,0],[0,0,1]],0,200);
 assert.ok(svg.includes('静止 100 回')&&svg.includes('移動 100 回')&&svg.includes('不明 1 回'));
 assert.ok(!/NaN|Infinity/.test(Calculator.timelineSVG([[0,0,0]],0,200)));
 assert.equal(Calculator.pct(null),'—');
});
