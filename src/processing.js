const WORKER='http://127.0.0.1:8765';
const KEY='lidar:active-dem:v2';
export async function workerRequest(path,options={}){
  let response;
  try{response=await fetch(WORKER+path,{...options,signal:options.signal||AbortSignal.timeout(15000)});}
  catch{throw Error('Cannot reach this laptop’s processing worker. Start worker/start-worker.ps1 and allow local-network access in your browser if requested.');}
  const data=await response.json();if(!response.ok)throw Error(data.error||'Worker request failed');return data;
}
export async function startProcessing(config){
  const health=await workerRequest('/health');if(!health.ready)throw Error('The laptop worker is online, but its PDAL/GDAL runtime is incomplete.');
  const job=await workerRequest('/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
  const handle={id:job.id,token:job.token,metadata:{name:config.title,county_geoid:config.geoid,resolution:config.resolution,survey:config.survey,tile_count:config.records.length}};
  localStorage.setItem(KEY,JSON.stringify(handle));return handle;
}
export function storedProcessing(){try{return JSON.parse(localStorage.getItem(KEY));}catch{return null;}}
export async function readProcessing(handle){return workerRequest('/jobs/'+handle.id,{headers:{Authorization:'Bearer '+handle.token}});}
export async function cancelProcessing(handle){return workerRequest('/jobs/'+handle.id+'/cancel',{method:'POST',headers:{Authorization:'Bearer '+handle.token}});}
export function resultURL(handle){return WORKER+'/jobs/'+handle.id+'/download?token='+encodeURIComponent(handle.token);}
export function forgetProcessing(){localStorage.removeItem(KEY);}
