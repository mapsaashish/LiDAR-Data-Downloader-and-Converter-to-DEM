(function(root){
  // Index boundary segments by latitude. Only nearby segments are inspected for
  // rectangle intersection and ray crossings; holes and multipolygons are kept.
  class CountyIndex{
    constructor(feature){
      const geometry=feature.geometry;
      const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:null;
      if(!polygons)throw Error('Area must be a polygon or multipolygon.');
      this.polygonCount=polygons.length;this.segments=[];this.minY=Infinity;this.maxY=-Infinity;
      polygons.forEach((rings,polygon)=>rings.forEach(ring=>{
        for(let n=0;n<ring.length-1;n++){
          const a=ring[n],b=ring[n+1];
          const s={a,b,polygon,minX:Math.min(a[0],b[0]),maxX:Math.max(a[0],b[0]),minY:Math.min(a[1],b[1]),maxY:Math.max(a[1],b[1])};
          this.segments.push(s);this.minY=Math.min(this.minY,s.minY);this.maxY=Math.max(this.maxY,s.maxY);
        }
      }));
      if(!this.segments.length)throw Error('Area boundary is empty.');
      this.bins=Array.from({length:256},()=>[]);
      for(const s of this.segments)for(let n=this.row(s.minY);n<=this.row(s.maxY);n++)this.bins[n].push(s);
    }
    row(y){return Math.max(0,Math.min(255,Math.floor((y-this.minY)/(this.maxY-this.minY||1)*256)));}
    intersects(b){
      if(b.maxY<this.minY||b.minY>this.maxY)return false;
      const seen=new Set();
      for(let n=this.row(b.minY);n<=this.row(b.maxY);n++)for(const s of this.bins[n]){
        if(seen.has(s))continue;seen.add(s);
        if(s.maxX<b.minX||s.minX>b.maxX||s.maxY<b.minY||s.minY>b.maxY)continue;
        if(segmentInRectangle(s,b))return true;
      }
      const x=(b.minX+b.maxX)/2,y=(b.minY+b.maxY)/2,inside=new Uint8Array(this.polygonCount);
      for(const s of this.bins[this.row(y)]){
        if((s.a[1]>y)!==(s.b[1]>y)&&x<(s.b[0]-s.a[0])*(y-s.a[1])/(s.b[1]-s.a[1])+s.a[0])inside[s.polygon]^=1;
      }
      return inside.some(Boolean);
    }
  }
  function segmentInRectangle({a,b},r){
    const dx=b[0]-a[0],dy=b[1]-a[1];let lo=0,hi=1;
    const p=[-dx,dx,-dy,dy],q=[a[0]-r.minX,r.maxX-a[0],a[1]-r.minY,r.maxY-a[1]];
    for(let n=0;n<4;n++){
      if(p[n]===0){if(q[n]<0)return false;continue;}
      const t=q[n]/p[n];if(p[n]<0)lo=Math.max(lo,t);else hi=Math.min(hi,t);if(lo>hi)return false;
    }
    return true;
  }
  if(typeof module!=='undefined')module.exports={CountyIndex};else root.CountyIndex=CountyIndex;
})(globalThis);
