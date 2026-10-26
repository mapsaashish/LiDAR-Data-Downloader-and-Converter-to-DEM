const {test}=require('node:test');
const assert=require('node:assert/strict');
const catalogModule={exports:{}};new Function('module',require('node:fs').readFileSync('./src/catalog.js','utf8').replace('export async function','async function')+';module.exports={loadCatalog};')(catalogModule);const {loadCatalog}=catalogModule.exports;
function service({total=2475,failOffset=-1,changeOffset=-1,delay=5}={}){
  let active=0,peak=0;const calls=[];
  return {calls,get peak(){return peak;},request:async(url,{signal})=>{
    const offset=Number(new URL(url).searchParams.get('offset'));calls.push(offset);active++;peak=Math.max(peak,active);
    try{await new Promise((resolve,reject)=>{const timer=setTimeout(resolve,delay);signal.addEventListener('abort',()=>{clearTimeout(timer);reject(signal.reason);},{once:true});});signal.throwIfAborted();
      return {ok:offset!==failOffset,status:offset===failOffset?403:200,json:async()=>({total:offset===changeOffset?total+1:total,items:Array.from({length:Math.max(0,Math.min(500,total-offset))},(_,n)=>({url:'tile-'+(offset+n)}))})};
    }finally{active--;}
  }};
}
test('retrieves every page concurrently and preserves source order',async()=>{
  const api=service();let completed=0;
  const rows=await loadCatalog({bbox:[0,0,1,1],request:api.request,onProgress:p=>{assert.ok(p.completed>=completed);completed=p.completed;}});
  assert.equal(api.peak,4);assert.deepEqual(api.calls,[0,500,1000,1500,2000]);assert.equal(rows.length,2475);assert.equal(completed,2475);assert.deepEqual(rows.map(r=>r.url),Array.from({length:2475},(_,i)=>'tile-'+i));
});
test('page filtering and duplicate removal retain deterministic results',async()=>{
  const api=service({total:1100});const rows=await loadCatalog({bbox:[0,0,1,1],request:api.request,processPage:async rows=>[...rows.filter(i=>Number(i.url.split('-')[1])%2===0),{url:'duplicate'}]});assert.equal(rows.length,551);assert.equal(rows.filter(r=>r.url==='duplicate').length,1);
});
test('failed page rejects instead of returning a partial list',async()=>{
  const api=service({failOffset:1000});await assert.rejects(loadCatalog({bbox:[0,0,1,1],request:api.request}),/HTTP 403/);
});
test('catalog changes reject instead of silently skipping results',async()=>{
  const api=service({changeOffset:500});await assert.rejects(loadCatalog({bbox:[0,0,1,1],request:api.request}),/catalog changed/);
});
test('cancellation aborts parallel requests',async()=>{
  const api=service({delay:30}),controller=new AbortController();const job=loadCatalog({bbox:[0,0,1,1],request:api.request,signal:controller.signal});setTimeout(()=>controller.abort(),40);await assert.rejects(job,{name:'AbortError'});
});
test('empty county returns an empty complete result',async()=>{
  const api=service({total:0});assert.deepEqual(await loadCatalog({bbox:[0,0,1,1],request:api.request}),[]);assert.deepEqual(api.calls,[0]);
});
test('rate limit retries its page without losing results',async()=>{
  const api=service({total:1});let calls=0;const rows=await loadCatalog({bbox:[0,0,1,1],request:async(...args)=>++calls===1?{ok:false,status:429,headers:{get:()=>null}}:api.request(...args)});assert.equal(calls,2);assert.equal(rows.length,1);
});
