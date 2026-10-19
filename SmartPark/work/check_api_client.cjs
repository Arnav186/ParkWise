const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8').split('\nfunction toast')[0];
async function check(response, expected) {
  const context = vm.createContext({fetch: async () => response});
  vm.runInContext(source + '\nglobalThis.request = api;', context);
  if (expected) await assert.rejects(context.request('/api/sessions'), expected);
  else assert.equal((await context.request('/api/sessions')).sessions.length, 0);
}
(async () => {
  await check({status:404, headers:new Headers({'content-type':'text/html'})}, /Restart python server.py/);
  await check({status:200, headers:new Headers({'content-type':'text/html'})}, /static preview/);
  await check({status:200, ok:true, headers:new Headers({'content-type':'application/json'}), json:async()=>({sessions:[]})});
  await check({status:400, ok:false, headers:new Headers({'content-type':'application/json'}), json:async()=>({error:'Capacity exceeded'})}, /Capacity exceeded/);
  console.log('PASS: HTML errors, valid JSON, and API validation errors.');
})().catch(error => { console.error(error); process.exitCode = 1; });
