importScripts('spatial.js');
let index;
onmessage=({data})=>{
  try{
    if(data.type==='init'){
      index=new CountyIndex(data.shape);
      postMessage({id:data.id,rows:[]});return;
    }
    const rows=[];
    for(const i of data.rows){
      let url;try{const x=new URL(i.downloadLazURL||i.downloadURL);if(x.protocol!=='https:'||!(/(^|\.)usgs\.gov$/.test(x.hostname)||x.hostname.endsWith('.amazonaws.com')))continue;url=x.href;}catch{continue;}
      const b=i.boundingBox;
      if(!b||![b.minX,b.minY,b.maxX,b.maxY].every(Number.isFinite)||b.minX>b.maxX||b.minY>b.maxY)throw Error('A catalog tile is missing a valid boundary; complete results cannot be verified.');
      if(index.intersects(b))rows.push({...i,url});
    }
    postMessage({id:data.id,rows});
  }catch(error){postMessage({id:data.id,error:error.message});}
};
