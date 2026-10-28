/* Controller-owned browser action interpreter. Never executed on the host. */
'use strict';
const fs=require('node:fs'), path=require('node:path'), readline=require('node:readline');
const config=JSON.parse(fs.readFileSync('browser.json','utf8'));
const temp=path.join(process.cwd(),'.runtime','tmp');fs.mkdirSync(temp,{recursive:true});
process.env.TEMP=process.env.TMP=process.env.TMPDIR=temp;
const {chromium}=require(config.library);
let pending=[],chain=Promise.resolve();
readline.createInterface({input:process.stdin}).on('line',line=>{const job=pending.shift();if(job){try{job.resolve(JSON.parse(line));}catch(e){job.reject(e);}}});
function request(req){const p=()=>new Promise((resolve,reject)=>{pending.push({resolve,reject});process.stdout.write(JSON.stringify({kind:'request',request:req})+'\n');});const result=chain.then(p);chain=result.catch(()=>{});return result;}
setTimeout(()=>process.exit(124),110000).unref();
async function eventually(predicate,step){
 const deadline=Date.now()+6000;let lastError;
 do{
  try{if(await predicate())return;}catch(error){lastError=error;}
  await new Promise(resolve=>setTimeout(resolve,50));
 }while(Date.now()<deadline);
 throw Error('Timeout 6000ms waiting for '+JSON.stringify(step)+(lastError?': '+String(lastError):''));
}

(async()=>{
 const browser=await chromium.launch({executablePath:config.executable,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const context=await browser.newContext({viewport:{width:1280,height:820}});
 let inFlight=0;const delays=[],delayEvidence=[],routeErrors=[],delayedRequests=new WeakMap(),abortedRequests=new WeakSet();
 context.on('requestfailed',request=>{
  if(request.failure()?.errorText?.includes('ERR_ABORTED')){
   abortedRequests.add(request);
   const observation=delayedRequests.get(request);if(observation)observation.aborted_by_application=true;
  }
 });
 await context.route('**/*',async route=>{
  const r=route.request(),url=new URL(r.url());
  if(url.hostname!=='repair.local')return route.abort();
  inFlight++;
  try{
   const index=delays.findIndex(d=>r.method()==='GET'&&url.pathname===d.target);
   const delay=index<0?null:delays.splice(index,1)[0];
   const observation=delay?{target:delay.target,path:url.pathname+url.search,ms:delay.ms,consumed:true,completed:false}:null;
   if(observation){delayEvidence.push(observation);delayedRequests.set(r,observation);}
   const x=await request({method:r.method(),path:url.pathname+url.search,body:(r.postDataBuffer()||Buffer.alloc(0)).toString('base64'),headers:r.headers()});
   if(observation)observation.actual_backend_status=x.status;
   if(delay)await new Promise(resolve=>setTimeout(resolve,delay.ms));
   try{await route.fulfill({status:x.status,headers:x.headers,body:Buffer.from(x.body,'base64')});}
   catch(error){if(r.failure()?.errorText?.includes('ERR_ABORTED')){if(observation)observation.aborted_by_application=true;}else throw error;}
   if(observation){observation.completed=true;if(abortedRequests.has(r)||r.failure()?.errorText?.includes('ERR_ABORTED'))observation.aborted_by_application=true;}
  }catch(error){routeErrors.push(String(error));try{await route.abort()}catch(_){};
  }finally{inFlight--;}
 });
 const page=await context.newPage();page.setDefaultTimeout(6000);
 const observations=[];let status='PASS';
 try{
  await page.goto('http://repair.local/');
  for(const s of config.steps){
   const node=s.selector?page.locator(s.selector):null;
   if(s.op==='fill')await node.fill(s.value);
   else if(s.op==='click')await node.click();
   else if(s.op==='select')await node.selectOption(s.value);
   else if(s.op==='press')await node.press(s.value);
   else if(s.op==='delay_next')delays.push({target:s.target,ms:s.ms});
   else if(s.op==='settle'){
    await new Promise(resolve=>setTimeout(resolve,50));
    await eventually(async()=>inFlight===0&&delays.length===0,s);
    await new Promise(resolve=>setTimeout(resolve,50));
    await eventually(async()=>inFlight===0&&delays.length===0,s);
   }
   else if(s.op==='wait_text')await eventually(async()=>await node.count()>0&&(await node.first().textContent({timeout:250})).includes(s.value),s);
   else if(s.op==='assert_text')await eventually(async()=>await node.count()===1&&(await node.innerText({timeout:250})).includes(s.value),s);
   else if(s.op==='assert_count')await eventually(async()=>await node.count()===s.count,s);
   else if(s.op==='reload')await page.reload();
   else throw Error('Unsupported action');
   observations.push({op:s.op,selector:s.selector||null,status:'PASS'});
  }
 }catch(e){status='FAIL';observations.push({status:'FAIL',step:config.steps[observations.length],detail:String(e)});}
 if(delays.length||delayEvidence.some(x=>!x.completed)){status='FAIL';observations.push({status:'FAIL',detail:'A requested actual response delay was not consumed and completed'});}
 if(routeErrors.length){status='FAIL';observations.push({status:'FAIL',detail:'Actual request bridge failed',errors:routeErrors});}
 await page.screenshot({path:'desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:'mobile.png',fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
 if(overflow){status='FAIL';observations.push({status:'FAIL',detail:'390px horizontal overflow'});}
 process.stdout.write(JSON.stringify({kind:'result',result:{status,observations,overflow,delayed_responses:delayEvidence}})+'\n');
 await browser.close();
})().catch(e=>{process.stdout.write(JSON.stringify({kind:'result',result:{status:'FAIL',detail:String(e)}})+'\n');process.exitCode=1;});
