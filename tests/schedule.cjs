const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const context=vm.createContext({});
vm.runInContext(fs.readFileSync('app/static/schedule.js','utf8'),context);
const S=context.ScheduleBuilder;
for(const value of ['0 9 * * *','30 8 * * 1-5','0 10 1 * *','0 10 L * *','*/15 * * * *','0 */3 * * *']) {
  const fields=S.decode(value);assert.ok(fields,value);
  assert.ok(S.describe(value).length>0);
  assert.ok(S.decode(S.encode(fields)));
}
assert.equal(S.encode({...S.decode('0 9 * * *'),mode:'weekly',days:['1','3','5']}),'0 9 * * 1,3,5');
assert.throws(()=>S.encode({...S.decode('0 9 * * *'),mode:'weekly',days:[]}));
assert.equal(S.decode('0 9 1,15 * *'),null); // preserve complex legacy schedules, don't silently simplify
assert.equal(S.decode('0 9 * 1,6 *'),null);
console.log('Visual schedule patterns and legacy detection passed');
