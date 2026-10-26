import { workerRequest, startProcessing, storedProcessing, readProcessing, cancelProcessing, resultURL, forgetProcessing } from './processing.js';
import { loadCatalog } from './catalog.js';
const $=id=>document.getElementById(id);
const census='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer';
let features=[],countyShape=null,items=[],visible=0,searchController=null,downloadController=null,busy=false;
const map=window.L?L.map('map',{preferCanvas:true}).setView([37.8,-96],4):null;
if(map)L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'© OpenStreetMap contributors',maxZoom:19}).addTo(map);
const boundary=map?L.geoJSON(null,{style:{color:'#176c58',weight:2.5,fillOpacity:.06}}).addTo(map):null;
const tileLayer=map?L.layerGroup().addTo(map):null;
const fmt=n=>n>=1e9?(n/1e9).toFixed(2)+' GB':n>=1e6?(n/1e6).toFixed(1)+' MB':n.toLocaleString()+' B';
const message=s=>$('status').textContent=s;
function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
async function json(url,signal){const r=await fetch(url,{signal:signal||AbortSignal.timeout(90000)});if(!r.ok)throw Error('Service returned HTTP '+r.status+'. Please retry later.');const d=await r.json();if(d.error)throw Error(d.error.message||'Boundary service error');if(d.errors?.length)throw Error(d.errors.join('; '));return d;}
async function query(layer,params){return json(census+'/'+layer+'/query?'+new URLSearchParams({f:'json',...params}));}
function reset(){items=[];if(typeof resetDEM==='function')resetDEM();visible=0;$('list').replaceChildren();$('list').className='empty';$('list').textContent='Find LiDAR to load all matching source tiles.';$('tiles').textContent='—';$('size').textContent='—';$('count').textContent='No search yet';$('more').hidden=true;$('download').disabled=true;$('script').disabled=true;tileLayer?.clearLayers();}
function controls(){window.dispatchEvent(new Event('lidar:selection'));const lock=busy;$('state').disabled=lock;$('county').disabled=lock||!features.length;$('search').disabled=lock||!countyShape;$('download').disabled=lock||!items.length;$('script').disabled=lock||!items.length;if(typeof updateAreaControls==='function')updateAreaControls();if(typeof updateDEM==='function')updateDEM();}
async function loadCounties(){if(typeof clearArea==='function')clearArea();reset();countyShape=null;boundary?.clearLayers();$('search').disabled=true;$('county').disabled=true;$('county').replaceChildren(option('','Loading counties…'));message('Loading counties…');try{const d=await query(1,{where:`STATE='${$('state').value}'`,outFields:'NAME,GEOID',returnGeometry:'false',orderByFields:'NAME'});features=d.features||[];$('county').replaceChildren(option('','Choose a county'),...features.map(f=>option(f.attributes.GEOID,f.attributes.NAME)));$('county').disabled=false;message('Choose a county to view its boundary.');}catch(e){message(e.message+' Change state to retry.');}}
let selection=0;
async function loadBoundary(){const seq=++selection;if(typeof clearArea==='function')clearArea();reset();countyShape=null;$('search').disabled=true;boundary?.clearLayers();if(!$('county').value)return;message('Loading county boundary…');try{const d=await json(census+'/1/query?'+new URLSearchParams({f:'geojson',where:`GEOID='${$('county').value}'`,outFields:'NAME,GEOID',returnGeometry:'true',outSR:'4326'}));if(seq!==selection)return;if(!d.features?.length)throw Error('County boundary not found.');countyShape=d.features[0];boundary?.addData(countyShape);if(boundary)map.fitBounds(boundary.getBounds(),{padding:[25,25]});$('heading').textContent=countyShape.properties.NAME+', '+$('state').selectedOptions[0].textContent;controls();message('County selected. Find all available LiDAR source tiles.');}catch(e){message(e.message+' Select the county again to retry.');}}
function safeURL(u){try{const x=new URL(u);return x.protocol==='https:'&&(/(^|\.)usgs\.gov$/.test(x.hostname)||x.hostname.endsWith('.amazonaws.com'))?x.href:null;}catch{return null;}}
function filename(i,n){let name=new URL(i.url).pathname.split('/').pop();try{name=decodeURIComponent(name);}catch{}return String(n+1).padStart(6,'0')+'_'+name.replace(/[^a-zA-Z0-9._-]/g,'_');}
function geometryFilter(shape,signal){
  const worker=new Worker('/filter-worker.js'),pending=new Map();let id=0;
  const close=(reason=new DOMException('Search cancelled','AbortError'))=>{worker.terminate();for(const p of pending.values())p.reject(reason);pending.clear();};
  worker.onmessage=({data})=>{const p=pending.get(data.id);if(!p)return;pending.delete(data.id);data.error?p.reject(Error(data.error)):p.resolve(data.rows);};
  worker.onerror=()=>close(Error('Background boundary filtering failed to load. Reload to retry.'));
  signal.addEventListener('abort',()=>close(),{once:true});
  const send=data=>new Promise((resolve,reject)=>{signal.throwIfAborted();const key=++id;pending.set(key,{resolve,reject});worker.postMessage({...data,id:key});});
  return {ready:send({type:'init',shape}),processPage:rows=>send({type:'filter',rows}),close};
}
function tileDetails(item,pinned=false){
  const panel=document.createElement('div');panel.className='tile-detail';
  const title=document.createElement('strong');title.textContent=item.title||'USGS LiDAR tile';panel.append(title);
  const details=document.createElement('dl');
  const date=value=>{if(!value)return 'Not provided';const match=String(value).match(/^\d{4}-\d{2}-\d{2}/);return match?match[0]:'Not provided';};
  const fields=[['Source','USGS National Map · '+(item.sourceName||'3DEP')],['Survey',surveyKey(item).replaceAll('_',' ')],['Published',date(item.publicationDate)],['Updated',date(item.lastUpdated)],['Acquisition date','See survey metadata'],['Format',item.format||'LAS / LAZ'],['File size',Number(item.sizeInBytes)?fmt(Number(item.sizeInBytes)):'Not provided']];
  for(const [label,value] of fields){const dt=document.createElement('dt');dt.textContent=label;const dd=document.createElement('dd');dd.textContent=value;details.append(dt,dd);}panel.append(details);
  if(pinned&&item.metaUrl){try{const url=new URL(item.metaUrl);if(url.protocol==='https:'&&(/(^|\.)usgs\.gov$/.test(url.hostname)||/(^|\.)sciencebase\.gov$/.test(url.hostname))){const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener';link.textContent='View survey metadata ↗';panel.append(link);}}catch{}}
  if(!pinned){const hint=document.createElement('small');hint.textContent='Click the tile to pin details and open metadata.';panel.append(hint);}
  return panel;
}
async function search(){if(!countyShape||busy)return;if(!window.turf){message('Geometry library failed to load. Reload this page to retry.');return;}reset();busy=true;controls();searchController=new AbortController();$('cancel').hidden=false;let filter;const started=performance.now();try{
const searchArea=activeArea();filter=geometryFilter(searchArea,searchController.signal);
await filter.ready;
message('Searching USGS with up to 4 parallel catalog requests…');
const found=await loadCatalog({bbox:turf.bbox(searchArea),signal:searchController.signal,processPage:filter.processPage,onProgress:({completed,total,retrying})=>message(`${retrying?'USGS is busy; retrying with backoff…':'Parallel search ·'} ${completed.toLocaleString()} of ${total.toLocaleString()} catalog records checked.`)});
items=found;populateSurveys();$('tiles').textContent=items.length.toLocaleString();const size=items.reduce((s,i)=>s+(Number(i.sizeInBytes)||0),0);$('size').textContent=fmt(size)+(items.some(i=>!Number(i.sizeInBytes))?' + unknown':'');$('count').textContent=items.length.toLocaleString()+' matching tiles';message(items.length?('Search complete in '+((performance.now()-started)/1000).toFixed(1)+'s. All catalog pages retrieved with up to 4 parallel requests. Overlapping surveys included.'):'No published USGS point-cloud tiles found for this county.');$('list').className='';$('list').replaceChildren();if(!items.length){$('list').className='empty';$('list').textContent='No tiles found. Try another county.';}renderMore();if(map)items.forEach(i=>{const b=i.boundingBox;L.rectangle([[b.minY,b.minX],[b.maxY,b.maxX]],{weight:1,color:'#6e9254',fillOpacity:.12}).bindTooltip(()=>tileDetails(i),{sticky:true,direction:'top',opacity:.98,className:'lidar-tile-tooltip'}).bindPopup(()=>tileDetails(i,true),{maxWidth:370}).addTo(tileLayer);});
}catch(e){reset();message(e.name==='AbortError'?'Search cancelled. No partial download list retained.':'Search failed: '+e.message+' No partial results retained; retry the search.');}finally{filter?.close();busy=false;searchController=null;$('cancel').hidden=true;controls();}}
function renderMore(){const end=Math.min(visible+100,items.length);for(let n=visible;n<end;n++){const i=items[n],row=document.createElement('div');row.className='row';const icon=document.createElement('span');icon.className='fileicon';icon.textContent=i.format||'LAZ';const text=document.createElement('div');text.className='filetext';const title=document.createElement('strong');title.textContent=i.title;const detail=document.createElement('small');detail.textContent=(i.sizeInBytes?fmt(Number(i.sizeInBytes)):'Size unknown')+' · Published '+(i.publicationDate||'unknown');text.append(title,detail);const a=document.createElement('a');a.href=i.url;a.textContent='Download ↗';a.target='_blank';a.rel='noopener';row.append(icon,text,a);$('list').append(row);}visible=end;$('more').hidden=visible>=items.length;}
function save(content,name,type){const a=document.createElement('a'),url=URL.createObjectURL(new Blob([content],{type}));a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function bulkScript(){const records=items.map((i,n)=>({url:i.url,name:filename(i,n),size:Number(i.sizeInBytes)||0}));const payload=btoa(JSON.stringify(records));const script=`# LiDAR Atlas - full source tile downloader. Requires Windows 10/11 curl.exe.\r\n$ErrorActionPreference = 'Stop'\r\n$folder = Read-Host 'Destination folder for all LiDAR files'\r\nif ([string]::IsNullOrWhiteSpace($folder)) { throw 'A destination folder is required' }\r\nNew-Item -ItemType Directory -Path $folder -Force | Out-Null\r\n$records = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${payload}')) | ConvertFrom-Json\r\n$failed = @()\r\n$index = 0\r\nforeach ($item in $records) {\r\n  $index++\r\n  $target = Join-Path $folder $item.name\r\n  $partial = $target + '.part'\r\n  Write-Host ('[' + $index + '/' + $records.Count + '] ' + $item.name)\r\n  if ((Test-Path -LiteralPath $target) -and ($item.size -gt 0) -and ((Get-Item -LiteralPath $target).Length -eq $item.size)) { Write-Host 'Already complete'; continue }\r\n  & curl.exe --fail --location --retry 6 --retry-delay 10 --connect-timeout 60 --continue-at - --output $partial $item.url\r\n  if ($LASTEXITCODE -eq 0) {\r\n    $actual = (Get-Item -LiteralPath $partial).Length\r\n    if (($item.size -gt 0) -and ($actual -ne $item.size)) { Write-Warning 'Size mismatch; retained partial file'; $failed += $item.url }\r\n    else { Move-Item -LiteralPath $partial -Destination $target -Force }\r\n  } else { $failed += $item.url; Write-Warning 'Download failed; rerun this script to resume' }\r\n  Start-Sleep -Seconds 1\r\n}\r\nif ($failed.Count) { $failed | Set-Content -LiteralPath (Join-Path $folder 'failed-urls.txt'); Write-Warning ($failed.Count.ToString() + ' files incomplete. Rerun to resume.') }\r\nelse { Write-Host 'All LiDAR tiles downloaded successfully.' }\r\nRead-Host 'Press Enter to close'\r\n`;save(script,'lidar-'+$('county').value+'-download-all.ps1','text/plain');$('transfer').textContent='Bulk downloader saved. Run it with PowerShell and choose a destination folder. It downloads all '+items.length.toLocaleString()+' tiles and resumes partial files when rerun.';}
async function downloadAll(){if(busy||!items.length)return;if(!window.showDirectoryPicker){$('transfer').textContent='This browser cannot save directly to a folder. Use Chrome or Edge, or click “Get bulk downloader (.ps1)” to download every file on Windows.';return;}let dir;try{dir=await showDirectoryPicker({mode:'readwrite'});}catch(e){$('transfer').textContent=e.name==='AbortError'?'Folder selection cancelled.':e.message;return;}busy=true;controls();downloadController=new AbortController();$('cancel').hidden=false;$('progress').hidden=false;$('progress').max=items.length;$('progress').value=0;let done=0;const failed=[];for(let n=0;n<items.length;n++){if(downloadController.signal.aborted)break;const i=items[n];let writable;try{$('transfer').textContent=`Downloading ${n+1} of ${items.length}: ${i.title}`;const name=filename(i,n);try{const existing=await dir.getFileHandle(name);const file=await existing.getFile();if(Number(i.sizeInBytes)>0&&file.size===Number(i.sizeInBytes)){done++;$('progress').value=n+1;continue;}}catch{}const r=await fetch(i.url,{signal:downloadController.signal});if(!r.ok||!r.body)throw Error('Source HTTP '+r.status);const file=await dir.getFileHandle(name+'.part',{create:true});writable=await file.createWritable();const reader=r.body.getReader();let received=0;for(;;){const {done:ended,value}=await reader.read();if(ended)break;await writable.write(value);received+=value.byteLength;$('transfer').textContent=`File ${n+1}/${items.length} · ${fmt(received)}${i.sizeInBytes?' / '+fmt(Number(i.sizeInBytes)):''} · ${done} completed`;}await writable.close();writable=null;if(Number(i.sizeInBytes)>0&&received!==Number(i.sizeInBytes))throw Error('File size does not match catalog');const partial=await file.getFile();const finalHandle=await dir.getFileHandle(name,{create:true});await partial.stream().pipeTo(await finalHandle.createWritable());await dir.removeEntry(name+'.part');done++;}catch(e){if(writable)await writable.abort().catch(()=>{});if(downloadController.signal.aborted)break;failed.push({name:filename(i,n),reason:e.message});if(done===0&&failed.length===1){$('transfer').textContent='The source server blocked or failed the browser transfer. Use the bulk downloader for all files, with retries and resume support.';break;}}$('progress').value=n+1;}
const cancelled=downloadController.signal.aborted;$('transfer').textContent=(cancelled?'Cancelled. ':done===items.length?'All tiles downloaded. ':'Download stopped or finished with failures. ')+`${done} of ${items.length} files saved successfully.`+(done<items.length?' Use the bulk downloader to fetch all files reliably. Browser retries skip complete files with matching sizes.':'');busy=false;downloadController=null;$('cancel').hidden=true;controls();}
$('state').onchange=()=>{selection++;loadCounties();};$('county').onchange=loadBoundary;$('search').onclick=search;$('more').onclick=renderMore;$('script').onclick=bulkScript;$('download').onclick=downloadAll;$('cancel').onclick=()=>{searchController?.abort();downloadController?.abort();};
async function init(){try{const d=await query(0,{where:'1=1',outFields:'STATE,NAME',returnGeometry:'false',orderByFields:'NAME'});if(!d.features?.length)throw Error('No states returned');$('state').replaceChildren(...d.features.map(f=>option(f.attributes.STATE,f.attributes.NAME)));$('state').disabled=false;$('state').value='12';await loadCounties();}catch(e){message('Unable to load states: '+e.message+' Reload to retry.');}}

let aoiShape=null,drawing=null;
const areaLayer=map?L.geoJSON(null,{style:{color:'#d17c29',weight:3,fillColor:'#e7a04a',fillOpacity:.15}}).addTo(map):null;
function activeArea(){return aoiShape||countyShape;}
function clearArea(){drawing?.disable();drawing=null;aoiShape=null;areaLayer?.clearLayers();$('aoi-info').textContent='Use the whole county, or draw a smaller area on the map.';}
function updateAreaControls(){for(const id of ['polygon','rectangle'])$(id).disabled=busy||!countyShape||!L.Draw;$('fullcounty').disabled=busy||!aoiShape;}
function drawArea(kind){if(busy||!countyShape||!map||!L.Draw)return;drawing?.disable();drawing=kind==='polygon'?new L.Draw.Polygon(map,{allowIntersection:false,showArea:true,shapeOptions:{color:'#d17c29'}}):new L.Draw.Rectangle(map,{shapeOptions:{color:'#d17c29'}});drawing.enable();$('aoi-info').textContent=kind==='polygon'?'Click to place vertices; click the first vertex to finish. Esc cancels.':'Drag a rectangle on the map. Esc cancels.';}
if(map)map.on('draw:created',event=>{
  if(busy||!countyShape)return;
  try{
    const clipped=turf.intersect(turf.featureCollection([countyShape,event.layer.toGeoJSON()]));
    if(!clipped||turf.area(clipped)<1){$('aoi-info').textContent='Draw an area inside the selected county. Existing selection retained.';return;}
    aoiShape=clipped;areaLayer.clearLayers();areaLayer.addData(clipped);reset();
    $('aoi-info').textContent='Custom area · '+(turf.area(clipped)/1e6).toFixed(2)+' km² within the county. Redraw to replace it.';
    $('heading').textContent=countyShape.properties.NAME+' · Custom area';
    message('Custom area selected. Search again for tiles intersecting this area.');controls();
  }catch(error){$('aoi-info').textContent='Unable to use that drawing: '+error.message;}
});
$('polygon').onclick=()=>drawArea('polygon');$('rectangle').onclick=()=>drawArea('rectangle');
$('fullcounty').onclick=()=>{if(busy)return;clearArea();reset();$('heading').textContent=countyShape.properties.NAME+', '+$('state').selectedOptions[0].textContent;message('Full county selected. Search again to retrieve all county tiles.');controls();};
function surveyKey(i){const parts=new URL(i.url).pathname.split('/');const at=parts.indexOf('Projects');if(at<0)return i.title.replace(/\s+\S+$/,'');return parts[at+1]==='legacy'?parts[at+2]:parts[at+1];}
let allSurveys=new Map(),streamingKeys=null,sourceCheck=0,checkingSources=false;
function showSurveyChoices(){
  const previous=$('dem-survey').value,stream=$('dem-mode').value==='stream';
  const choices=Array.from(allSurveys).filter(([key])=>!stream||streamingKeys?.has(key)).sort(([a],[b])=>a.localeCompare(b));
  const placeholder=stream&&checkingSources?'Checking available streaming sources…':choices.length?'Choose an available survey':'No available sources for this mode';
  $('dem-survey').replaceChildren(option('',placeholder),...choices.map(([key,rows])=>option(key,key.replaceAll('_',' ')+' · '+rows.length+' tiles')));
  if(choices.some(([key])=>key===previous))$('dem-survey').value=previous;else if(choices.length===1)$('dem-survey').value=choices[0][0];
  updateDEM();
}
async function populateSurveys(){
  const generation=++sourceCheck;allSurveys=new Map();streamingKeys=null;checkingSources=true;
  for(const i of items){const key=surveyKey(i);if(!allSurveys.has(key))allSurveys.set(key,[]);allSurveys.get(key).push(i);}
  showSurveyChoices();$('dem-source-status').textContent='Verifying streaming availability. Only usable sources will be shown.';
  const surveys=Array.from(allSurveys,([key,rows])=>{const folders=new Map();for(const row of rows){const parts=new URL(row.url).pathname.split('/'),at=parts.findIndex(p=>['LAS','LAZ'].includes(p.toUpperCase())),folder=parts.slice(0,at).join('/');folders.set(folder,{url:row.url});}return {key,records:Array.from(folders.values())};});
  try{
    const result=await workerRequest('/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({surveys}),signal:AbortSignal.timeout(90000)});
    if(generation!==sourceCheck)return;
    streamingKeys=new Set(result.sources.filter(source=>source.available).map(source=>source.key));
    $('dem-source-status').textContent=streamingKeys.size+' verified streaming '+(streamingKeys.size===1?'survey':'surveys')+' available. Unavailable sources are hidden; legacy catalog names are resolved automatically.';
  }catch(error){if(generation!==sourceCheck)return;streamingKeys=new Set();$('dem-source-status').textContent='Could not verify streaming sources. '+error.message;}
  finally{if(generation===sourceCheck){checkingSources=false;showSurveyChoices();}}
}
function resetDEM(){sourceCheck++;allSurveys=new Map();streamingKeys=null;checkingSources=false;$('dem-source-status').textContent=''; $('dem-survey').replaceChildren(option('','Search LiDAR first'));$('dem-survey').disabled=true;updateDEM();}
function updateDEM(){const resolution=Number($('dem-resolution').value),survey=$('dem-survey').value;const ready=items.length>0&&!busy&&!($('dem-mode').value==='stream'&&checkingSources);$('dem-survey').disabled=!ready;$('dem-resolution').disabled=busy;$('dem-mode').disabled=busy;$('dem-job').disabled=!ready||![1,5,10,30].includes(resolution)||!survey;
  if(resolution&&activeArea()){const cells=Math.ceil(turf.area(activeArea())/(resolution*resolution));$('dem-estimate').textContent=`Approx. ${cells.toLocaleString()} cells inside your area at ${resolution} m (${fmt(cells*4)} uncompressed elevation values). The rectangular raster can be larger. The raw-file download total above does not represent streaming transfer size. Missing coverage stays NoData.`;}else $('dem-estimate').textContent='Choose a resolution and one source survey. This avoids mixing overlapping surveys and elevation references.';
}
async function checkWorker(){try{const health=await workerRequest('/health');$('worker-status').textContent=health.ready?'This laptop’s worker is online and ready.':'Worker is online, but the processing runtime is incomplete.';}catch(error){$('worker-status').textContent=error.message;}}
$('worker-check').onclick=checkWorker;checkWorker();
let currentDEM=null;
function demProgress(job,handle){
  $('dem-progress').hidden=false;$('dem-progress').value=job.percent||0;
  $('dem-percent').textContent=Math.round(job.percent||0)+'%';
  $('dem-status').textContent=job.message+' Progress is an estimate based on completed work units.';
  window.dispatchEvent(new CustomEvent('lidar:dem-state',{detail:{...handle.metadata,worker_job_id:handle.id,status:job.status,progress:job.percent||0,message:job.message,output_name:job.output_name||null}}));
}
async function followDEM(handle,autoDownload=true){
  currentDEM=handle;busy=true;controls();$('dem-cancel').hidden=false;$('dem-download').hidden=true;
  let errors=0;
  try{for(;;){
    let job;try{job=await readProcessing(handle);errors=0;}catch(error){if(++errors>=3)throw error;$('dem-status').textContent='Worker connection interrupted. Retrying…';await new Promise(resolve=>setTimeout(resolve,2500));continue;}
    demProgress(job,handle);
    if(job.status==='complete'){
      $('dem-download').href=resultURL(handle);$('dem-download').hidden=false;
      $('dem-status').textContent='100% · '+handle.metadata.name+' · '+handle.metadata.resolution+' m DEM created and verified. '+(autoDownload?'Starting the GeoTIFF download. ':'')+'Use Download DEM if your browser blocks the automatic download. Your browser shows final file-transfer progress.';
      if(autoDownload)$('dem-download').click();break;
    }
    if(['failed','cancelled'].includes(job.status)){forgetProcessing();$('dem-status').textContent=job.message;break;}
    await new Promise(resolve=>setTimeout(resolve,1500));
  }}catch(error){$('dem-status').textContent=error.message+' The job handle is retained; reload to reconnect.';}
  finally{busy=false;currentDEM=null;$('dem-cancel').hidden=true;controls();}
}
async function prepareDEM(){
  if($('dem-job').disabled)return;
  const resolution=Number($('dem-resolution').value),survey=$('dem-survey').value,shape=structuredClone(activeArea());
  const records=items.map((i,n)=>({i,n})).filter(({i})=>surveyKey(i)===survey).map(({i,n})=>({url:i.url,name:filename(i,n),size:Number(i.sizeInBytes)||0}));
  const center=turf.center(shape).geometry.coordinates,zone=Math.max(1,Math.min(60,Math.floor((center[0]+180)/6)+1));
  const config={resolution,crs:'EPSG:'+((center[1]>=0?32600:32700)+zone),records,area:shape,geoid:$('county').value,title:$('heading').textContent,survey,mode:$('dem-mode').value};
  busy=true;controls();$('dem-status').textContent='Connecting to this laptop’s processing worker…';
  try{const handle=await startProcessing(config);await followDEM(handle);}catch(error){$('dem-status').textContent=error.message;}finally{busy=false;controls();}
}
$('dem-cancel').onclick=async()=>{if(currentDEM){await cancelProcessing(currentDEM);$('dem-status').textContent='Cancelling processing…';}};
const previousDEM=storedProcessing();if(previousDEM)followDEM(previousDEM,false);
$('dem-mode').onchange=showSurveyChoices;$('dem-resolution').onchange=updateDEM;$('dem-survey').onchange=updateDEM;$('dem-job').onclick=prepareDEM;

init();

export function getSelection(){return {busy,ready:!!countyShape,state_fips:$('state').value,county_geoid:$('county').value,name:$('heading').textContent,geometry:aoiShape?structuredClone(aoiShape):null};}
export async function restoreSelection(saved){
  if(busy)throw Error('Wait for the current operation to finish.');
  $('state').value=saved.state_fips;
  if($('state').value!==saved.state_fips)throw Error('Saved state is unavailable.');
  selection++;await loadCounties();$('county').value=saved.county_geoid;
  if($('county').value!==saved.county_geoid)throw Error('Saved county is unavailable.');
  await loadBoundary();
  if(!countyShape)throw Error('Could not load the saved county.');
  if(saved.geometry){const clipped=turf.intersect(turf.featureCollection([countyShape,saved.geometry]));if(!clipped)throw Error('Saved area does not intersect this county.');aoiShape=clipped;areaLayer.clearLayers();areaLayer.addData(clipped);$('aoi-info').textContent='Restored custom area · '+(turf.area(clipped)/1e6).toFixed(2)+' km²';map.fitBounds(areaLayer.getBounds(),{padding:[25,25]});$('heading').textContent=countyShape.properties.NAME+' · Custom area';}
  controls();message('Saved area restored. Find LiDAR to refresh current availability.');
}
