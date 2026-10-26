  // Four bounded requests overlap network latency without flooding the public API.
  export async function loadCatalog({bbox,signal,onProgress=()=>{},processPage=async rows=>rows,request=fetch,concurrency=4}){
    const controller=new AbortController();
    const combined=signal?AbortSignal.any([signal,controller.signal]):controller.signal;
    const pageSize=500,pages=[];
    let completed=0,total=0,next=pageSize;
    const pause=(ms)=>new Promise((resolve,reject)=>{combined.throwIfAborted();const stop=()=>{clearTimeout(timer);reject(combined.reason);};const timer=setTimeout(()=>{combined.removeEventListener('abort',stop);resolve();},ms);combined.addEventListener('abort',stop,{once:true});});
    async function page(offset){
      for(let attempt=0;;attempt++){
        combined.throwIfAborted();
        const url='https://tnmaccess.nationalmap.gov/api/v1/products?'+new URLSearchParams({datasets:'Lidar Point Cloud (LPC)',bbox:bbox.join(','),max:String(pageSize),offset:String(offset)});
        const response=await request(url,{signal:AbortSignal.any([combined,AbortSignal.timeout(90000)])});
        if([429,500,502,503,504].includes(response.status)&&attempt<3){
          const retry=response.headers?.get('Retry-After');
          const seconds=retry&&Number.isFinite(Number(retry))?Number(retry):retry?(Date.parse(retry)-Date.now())/1000:0;
          onProgress({completed,total,retrying:true});
          await pause(Math.min(30000,Math.max(1000*2**attempt,seconds*1000||0)));continue;
        }
        if(!response.ok)throw Error('USGS returned HTTP '+response.status+'. Please retry later.');
        const data=await response.json();
        if(data.errors?.length)throw Error(data.errors.join('; '));
        if(!Number.isSafeInteger(data.total)||data.total<0||!Array.isArray(data.items))throw Error('Invalid catalog response.');
        return data;
      }
    }
    async function accept(data,offset){
      if(data.total!==total)throw Error('The USGS catalog changed during this search. Retry for a complete result.');
      if(data.items.length!==Math.min(pageSize,total-offset))throw Error('Catalog page is incomplete. Please retry.');
      pages[offset/pageSize]=await processPage(data.items);
      completed+=data.items.length;onProgress({completed,total,retrying:false});
    }
    try{
      const first=await page(0);total=first.total;
      onProgress({completed,total,retrying:false});
      // Start filtering the first page while fetching the remaining pages.
      const jobs=[accept(first,0)];
      for(let n=0;n<Math.min(concurrency,Math.ceil(total/pageSize)-1);n++)jobs.push((async()=>{
        while(next<total){const offset=next;next+=pageSize;await accept(await page(offset),offset);}
      })());
      // Settle every worker before returning or allowing another search to start.
      const outcomes=await Promise.allSettled(jobs.map(p=>p.catch(error=>{controller.abort();throw error;})));
      const failed=outcomes.find(x=>x.status==='rejected'&&x.reason?.name!=='AbortError')||outcomes.find(x=>x.status==='rejected');
      if(failed)throw failed.reason;
      combined.throwIfAborted();
      const seen=new Set();return pages.flat().filter(i=>{const key=i.url||i.downloadLazURL||i.downloadURL;if(seen.has(key))return false;seen.add(key);return true;});
    }catch(error){controller.abort();throw error;}
  }
