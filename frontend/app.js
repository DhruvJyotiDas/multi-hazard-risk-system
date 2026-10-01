/* Geographic Canvas terrain viewer. Geometry, imagery, rasters and routes are cached real data. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), data = window.OBSERVATORY_DATA;
  if (!data?.raster) { $('scene-layer').textContent='Analysis data unavailable. Run frontend/export_data.py.'; return; }
  const colors=['#1a9850','#a6d96a','#fdae61','#e34a33','#7f0000'];
  const names=['Very Low','Low','Moderate','High','Very High'];
  const layerNames={risk:'Composite multi-hazard risk',flood:'Flood susceptibility',landslide:'Landslide susceptibility',fire:'Fire susceptibility',exposure:'Settlement exposure'};
  const palettes={risk:colors,flood:['#d2e8dc','#a3d4d3','#74b8ce','#438bc2','#21517e'],landslide:['#d1dd9b','#bac077','#e0ad60','#bf733d','#7d382c'],fire:['#e2dc95','#e6c567','#eaa14f','#d85a37','#922c2b'],exposure:['#d7d3e4','#bcb7d5','#9b8cbd','#78579b','#533477']};
  const state={layer:'risk',opacity:.45,elevation:4,yaw:-.18,pitch:.8,zoom:1,flat:false,markers:true,routes:true,wireframe:false,hotspots:false,satellite:true,origin:3,destination:'Hospital',cx:0,cz:0};
  const clamp=(v,lo=0,hi=1)=>Math.max(lo,Math.min(hi,v));
  const fmt=n=>Number(n).toLocaleString('en-IN',{maximumFractionDigits:0});
  const total=data.stats.reduce((s,r)=>s+Number(r.area_km2),0),high=data.stats.filter(r=>Number(r.class_id)>=4).reduce((s,r)=>s+Number(r.area_km2),0);
  $('area').textContent=fmt(total);$('high-risk').textContent=(high/total*100).toFixed(1);
  $('high-area').textContent=`${fmt(high)} km² in elevated risk classes`;
  $('risk-callout').textContent=`${(high/total*100).toFixed(1)}% in elevated risk classes`;
  data.stats.forEach((r,i)=>{
    const row=document.createElement('div');row.className='distribution-row';
    const label=document.createElement('div');label.className='distribution-label';
    const dot=document.createElement('span');dot.className='dot';dot.style.background=colors[i];
    const name=document.createElement('span');name.textContent=r.class_name;
    const percent=document.createElement('span');percent.className='percent';percent.textContent=`${Number(r.percent_of_study_area).toFixed(1)}%`;
    label.append(dot,name,percent);const track=document.createElement('div');track.className='bar-track';
    const bar=document.createElement('div');bar.className='bar';bar.style.width=`${r.percent_of_study_area}%`;bar.style.background=colors[i];track.append(bar);
    const area=document.createElement('small');area.textContent=`${fmt(r.area_km2)} km²`;row.append(label,track,area);$('distribution').append(row);
  });
  const event=data.validation?.results?.find(r=>r.label==='verified');
  $('validation-status').textContent=data.validation?(data.validation.headline_passed?'PASS':'FAIL'):'N/A';
  $('validation-share').textContent=event?`${(event.zone_fraction_high_or_very_high*100).toFixed(1)}% high risk`:'No saved result';
  $('validation-badge').textContent=data.validation?(data.validation.headline_passed?'✓ VALIDATION PASSED':'VALIDATION FAILED'):'RESULT UNAVAILABLE';
  $('validation-detail').textContent=event?`${(event.zone_fraction_high_or_very_high*100).toFixed(1)}% of the 2 km event zone is High or Very High. Saved point class: ${event.point_class_name}.`:'Validation output unavailable.';
  data.weights.forEach(r=>{const row=document.createElement('div');row.className='weight-row';const label=document.createElement('span');label.textContent=r.criterion[0].toUpperCase()+r.criterion.slice(1);const weight=document.createElement('span');weight.className='mint';weight.textContent=`${(Number(r['AHP+Entropy (used)'])*100).toFixed(1)}%`;row.append(label,weight);$('weights').append(row);});
  $('data-status').textContent=`${data.metrics.length} route pairs · ${data.hotspots?.features.length||0} hotspots · ${data.raster.cols} × ${data.raster.rows} analysis grid. ${data.raster.elevation?'Real SRTM elevation.':'Elevation unavailable; showing flat geography.'} ${data.texture?'Sentinel-2 RGB: '+data.provenance.satellite_date_range.join(' to ')+'.':''}`;
  if(!data.raster.elevation)$('terrain-note').textContent='Real geographic risk grids; no elevation available. Fetch terrain to enable relief.';

  function decode(text){const raw=atob(text),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return bytes;}
  const raster=data.raster,grids=Object.fromEntries(Object.entries(raster.grids).map(([key,value])=>[key,decode(value)]));
  const classes=decode(raster.classes),elevationBytes=raster.elevation?decode(raster.elevation):null;
  const elevation=elevationBytes?new DataView(elevationBytes.buffer):null;
  const [west,south,east,north]=raster.bounds,aspect=(north-south)*110570/raster.width_m;
  const toWorld=([lon,lat])=>({x:(lon-west)/(east-west)*2-1,z:((north-lat)/(north-south)*2-1)*aspect});
  const toGeo=(x,z)=>({lon:west+(x+1)/2*(east-west),lat:north-(z/aspect+1)/2*(north-south)});
  function index(x,z){const col=Math.floor((x+1)/2*raster.cols),row=Math.floor((z/aspect+1)/2*raster.rows);return col>=0&&col<raster.cols&&row>=0&&row<raster.rows?row*raster.cols+col:-1;}
  function sample(layer,x,z){const i=index(x,z);return i>=0&&grids[layer]?.[i]!==255?grids[layer][i]/254:null;}
  function metersAt(x,z){
    if(!elevation)return 0;
    const fx=clamp((x+1)/2*raster.cols-.5,0,raster.cols-1),fy=clamp((z/aspect+1)/2*raster.rows-.5,0,raster.rows-1);
    const c=Math.floor(fx),r=Math.floor(fy),c1=Math.min(c+1,raster.cols-1),r1=Math.min(r+1,raster.rows-1),tx=fx-c,ty=fy-r;
    const value=(rr,cc)=>{const v=elevation.getInt16((rr*raster.cols+cc)*2,true);return v===-32768?0:v;};
    return value(r,c)*(1-tx)*(1-ty)+value(r,c1)*tx*(1-ty)+value(r1,c)*(1-tx)*ty+value(r1,c1)*tx*ty;
  }
  const terrain=(x,z)=>metersAt(x,z)/raster.width_m*2;
  function rings(geometry){if(!geometry)return [];if(geometry.type==='Polygon')return geometry.coordinates;if(geometry.type==='MultiPolygon')return geometry.coordinates.flat();return [];}
  const boundary=rings(data.aoi).map(ring=>ring.map(toWorld));
  function inRing(x,z,ring){let hit=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const a=ring[i],b=ring[j];if(((a.z>z)!==(b.z>z))&&x<(b.x-a.x)*(z-a.z)/(b.z-a.z)+a.x)hit=!hit;}return hit;}
  // Exact polygon membership prevents the mesh's border from becoming an oval or rectangular footprint.
  function inside(x,z){return index(x,z)>=0&&classes[index(x,z)]>0;}
  const vertices=[],faces=[],nx=97,nz=Math.max(40,Math.round(nx*aspect));
  for(let j=0;j<nz;j++)for(let i=0;i<nx;i++){const x=i/(nx-1)*2-1,z=(j/(nz-1)*2-1)*aspect;vertices.push({x,z,h:terrain(x,z),u:i/(nx-1),v:j/(nz-1)});}
  for(let j=0;j<nz-1;j++)for(let i=0;i<nx-1;i++){
    const a=j*nx+i,b=a+1,c=a+nx,d=c+1;
    for(const ids of [[a,c,b],[b,c,d]]){const x=ids.reduce((s,k)=>s+vertices[k].x,0)/3,z=ids.reduce((s,k)=>s+vertices[k].z,0)/3;
      if(inside(x,z)){const h=terrain(x,z),dx=(terrain(x+.008,z)-terrain(x-.008,z))/.016,dz=(terrain(x,z+.008)-terrain(x,z-.008))/.016;faces.push({ids,x,z,h,light:clamp(.9-dx*2+dz*1.2,.6,1.15)});}}
  }
  const canvas=$('terrain'),ctx=canvas.getContext('2d');if(!ctx){$('scene-layer').textContent='Canvas is unavailable.';return;}
  let width=1,height=1,pending=false,projected=[],triangles=[],textureReady=false;
  const texture=new Image();texture.onload=()=>{textureReady=true;draw();};if(data.texture)texture.src=data.texture;
  const baseScale=()=>Math.min(width*.44,height*.46/aspect);
  function project(x,z,h=terrain(x,z)){
    x-=state.cx;z-=state.cz;
    const cy=Math.cos(state.yaw),sy=Math.sin(state.yaw),rx=x*cy-z*sy,rz=x*sy+z*cy;
    const p=state.flat?0:state.pitch,e=state.flat?0:h*state.elevation;
    const scale=baseScale()*state.zoom;
    return {x:width*.47+rx*scale,y:height*.57+(rz*Math.cos(p)-e*Math.sin(p))*scale,depth:rz*Math.sin(p)+e*Math.cos(p)};
  }
  function rgb(hex){return hex.match(/[a-f0-9]{2}/gi).map(v=>parseInt(v,16));}
  // The display mesh is ~4 raster cells per face, so sampling one 100 m pixel per triangle aliases (speckle).
  // Each face instead takes the mean of the valid cells under its footprint; click inspection stays exact.
  const cellX=2/raster.cols,cellZ=2*aspect/raster.rows,classBreaks=data.stats.slice(1).map(r=>Number(r.index_min));
  function areaSample(layer,x,z,k=2){
    let sum=0,n=0;
    for(let dj=-k;dj<=k;dj++)for(let di=-k;di<=k;di++){const i=index(x+di*cellX,z+dj*cellZ);if(i<0||!classes[i])continue;const v=grids[layer]?.[i];if(v===undefined||v===255)continue;sum+=v/254;n++;}
    return n?sum/n:null;
  }
  const classOf=value=>classBreaks.filter(b=>value>=b).length+1;
  function faceColor(f){
    const value=areaSample(state.layer,f.x,f.z);if(value===null)return '#445c43';
    if(state.layer==='risk')return colors[classOf(value)-1];
    const n=value*4,k=Math.min(3,Math.floor(n)),t=n-k,a=rgb(palettes[state.layer][k]),b=rgb(palettes[state.layer][k+1]);return `rgb(${a.map((v,i)=>Math.round(v+(b[i]-v)*t)).join(',')})`;
  }
  let faceColors=faces.map(faceColor);
  function outline(points,color,lineWidth,dashed=false,closed=false,heightOffset=.0006){
    ctx.beginPath();points.forEach((p,i)=>{const screen=project(p.x,p.z,terrain(p.x,p.z)+heightOffset);i?ctx.lineTo(screen.x,screen.y):ctx.moveTo(screen.x,screen.y);});if(closed)ctx.closePath();ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.lineJoin='round';ctx.lineCap='round';ctx.setLineDash(dashed?[5,4]:[]);ctx.stroke();ctx.setLineDash([]);
  }
  function selectedRow(){return data.metrics.find(r=>Number(r.origin_id)===state.origin&&r.destination_type===state.destination);}
  function selectedRoutes(){return data.routes.features.filter(f=>Number(f.properties.origin_id)===state.origin&&f.properties.destination_type===state.destination);}
  const origins=[...new Map(data.metrics.map(r=>[Number(r.origin_id),r.origin])).entries()];
  const originMarkers=origins.map(([id,name])=>{const route=data.routes.features.find(f=>Number(f.properties.origin_id)===id);return route?{...toWorld(route.geometry.coordinates[0]),name,type:'origin',id}:null;}).filter(Boolean);
  const placeMarkers=(data.pois?.features||[]).filter(f=>f.properties.kind==='place'&&['Kalpetta','Mananthavady','Sulthan Bathery','Sultan Bathery'].includes(f.properties.name)).map(f=>({...toWorld(f.geometry.coordinates),name:f.properties.name,type:'place'}));
  const eventMarker=event?{...toWorld([event.lon,event.lat]),name:'2024 landslide zone',type:'event'}:null;
  let markerPositions=[];
  function drawMarkers(){
    markerPositions=[];
    const selected=selectedRoutes().find(f=>f.properties.route_type==='least_risk');
    const destination=selected?{...toWorld(selected.geometry.coordinates.at(-1)),name:selected.properties.destination,type:'destination'}:null;
    const markers=[...placeMarkers,...originMarkers,eventMarker,destination].filter(Boolean);
    // Small hospital dots show actual OSM point locations; named labels are limited to the selected route.
    ctx.fillStyle='#d1e2cd88';for(const feature of data.pois?.features||[]){if(feature.properties.kind!=='hospital')continue;const p=toWorld(feature.geometry.coordinates);if(!inside(p.x,p.z))continue;const s=project(p.x,p.z);ctx.fillRect(s.x-1,s.y-1,2,2);}
    const labels=[];
    for(const marker of markers){const p=project(marker.x,marker.z);if(p.x<8||p.y<100||p.x>width-8||p.y>height-50)continue;
      const selectedOrigin=marker.id===state.origin;
      ctx.beginPath();ctx.arc(p.x,p.y,marker.type==='place'?2:selectedOrigin?5:4,0,Math.PI*2);ctx.fillStyle=marker.type==='event'?'#ecaa72':marker.type==='destination'?'#f7f0d1':selectedOrigin?'#cdf7a3':'#cad9c0';ctx.fill();ctx.strokeStyle='#15271b';ctx.lineWidth=1.5;ctx.stroke();
      markerPositions.push({...marker,screen:p});ctx.font='9px "Segoe UI", sans-serif';
      const text=marker.name.length>35?marker.name.slice(0,32)+'…':marker.name,tw=ctx.measureText(text).width;
      const x=clamp(p.x+9,5,width-tw-18),y=p.y-11,box={x,y,w:tw+12,h:20};
      const collision=labels.some(b=>x<b.x+b.w&&x+box.w>b.x&&y<b.y+b.h&&y+box.h>b.y);
      if(collision&&!selectedOrigin&&marker.type!=='destination')continue;
      labels.push(box);ctx.fillStyle='#10231be0';ctx.fillRect(x,y,box.w,box.h);ctx.fillStyle=selectedOrigin?'#d8f5b4':'#dce7d1';ctx.fillText(text,x+6,y+13);
    }
  }
  function texturedTriangle(f,pts){
    const uv=f.ids.map(k=>({x:vertices[k].u*texture.width,y:vertices[k].v*texture.height}));
    const [a,b,c]=uv,[p,q,r]=pts,den=a.x*(b.y-c.y)+b.x*(c.y-a.y)+c.x*(a.y-b.y);if(Math.abs(den)<.001)return;
    const coefficient=(v1,v2,v3)=>[(v1*(b.y-c.y)+v2*(c.y-a.y)+v3*(a.y-b.y))/den,(v1*(c.x-b.x)+v2*(a.x-c.x)+v3*(b.x-a.x))/den,(v1*(b.x*c.y-c.x*b.y)+v2*(c.x*a.y-a.x*c.y)+v3*(a.x*b.y-b.x*a.y))/den];
    const x=coefficient(p.x,q.x,r.x),y=coefficient(p.y,q.y,r.y);
    ctx.save();ctx.clip();ctx.transform(x[0],y[0],x[1],y[1],x[2],y[2]);ctx.drawImage(texture,0,0);ctx.restore();
  }
  function render(){
    pending=false;ctx.clearRect(0,0,width,height);
    ctx.fillStyle='#15261f';ctx.fillRect(0,0,width,height);
    ctx.lineWidth=.5;ctx.strokeStyle='#b8cf9c10';
    for(let i=-1.4;i<=1.4;i+=.2){for(const points of [[{x:i,z:-1.2},{x:i,z:1.2}],[{x:-1.4,z:i},{x:1.4,z:i}]]){ctx.beginPath();points.forEach((p,k)=>{const s=project(p.x,p.z,0);k?ctx.lineTo(s.x,s.y):ctx.moveTo(s.x,s.y);});ctx.stroke();}}
    // A vertical edge under the true district boundary makes the DEM relief legible.
    if(!state.flat){for(const ring of boundary){for(let i=1;i<ring.length;i++){const a=ring[i-1],b=ring[i],p=project(a.x,a.z),q=project(b.x,b.z),r=project(b.x,b.z,0),s=project(a.x,a.z,0);ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.lineTo(r.x,r.y);ctx.lineTo(s.x,s.y);ctx.closePath();ctx.fillStyle='#1a3226';ctx.fill();}}}
    projected=vertices.map(v=>project(v.x,v.z,v.h));
    triangles=faces.map((f,i)=>({f,i,depth:f.ids.reduce((sum,k)=>sum+projected[k].depth,0)/3})).sort((a,b)=>a.depth-b.depth);
    for(const {f,i} of triangles){
      const pts=f.ids.map(k=>projected[k]);ctx.beginPath();pts.forEach((p,k)=>k?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();
      ctx.fillStyle='#547653';ctx.fill();
      if(state.satellite&&textureReady)texturedTriangle(f,pts);
      ctx.globalAlpha=state.opacity;ctx.fillStyle=faceColors[i];ctx.fill();ctx.strokeStyle=faceColors[i];ctx.lineWidth=.4;ctx.stroke();ctx.globalAlpha=1;
      const shade=state.flat?0:1-f.light;if(shade>0){ctx.globalAlpha=shade;ctx.fillStyle='#031810';ctx.fill();ctx.globalAlpha=1;}
      if(state.wireframe){ctx.strokeStyle='#dbe9c533';ctx.lineWidth=.4;ctx.stroke();}
    }
    boundary.forEach(r=>outline(r,'#c7dbacb0',1,false,true));
    if(state.hotspots)for(const feature of data.hotspots?.features||[])for(const ring of rings(feature.geometry))outline(ring.map(toWorld),'#ffa577b0',.85,false,true);
    if(state.routes){const selected=selectedRoutes();for(const type of ['shortest','least_risk']){const route=selected.find(f=>f.properties.route_type===type);if(!route)continue;const points=route.geometry.coordinates.map(toWorld);outline(points,'#0d211bcc',type==='shortest'?5.5:4.5);outline(points,type==='shortest'?'#f5ddc6':'#c9ff8f',type==='shortest'?3.5:2.5,type==='shortest');}}
    if(eventMarker){const ring=[];for(let i=0;i<=48;i++){const angle=i/48*Math.PI*2;ring.push(toWorld([event.lon+Math.cos(angle)*2000/(111320*Math.cos(event.lat*Math.PI/180)),event.lat+Math.sin(angle)*2000/110570]));}outline(ring,'#f1ac74b0',1,true,true);}
    if(state.markers)drawMarkers();else markerPositions=[];
    $('compass-arrow').style.transform=`rotate(${-state.yaw*180/Math.PI}deg)`;
    $('scene-state').textContent=state.flat?'2D / GEOGRAPHIC PLAN':`3D / ${state.elevation.toFixed(1)}× ELEVATION`;
    // Scale is in projected ground-plane distance, rather than a constant screen decoration.
    ctx.font='8px "Segoe UI", sans-serif';ctx.fillStyle='#c5d7bb';ctx.fillText('WGS84 · saved 100 m analysis',16,height-155);
  }
  function draw(){if(!pending){pending=true;requestAnimationFrame(render);}}
  function resize(){const bounds=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);width=bounds.width;height=bounds.height;canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
  new ResizeObserver(resize).observe($('scene'));
  function selectLayer(layer){state.layer=layer;document.querySelectorAll('[data-layer]').forEach(b=>{b.classList.toggle('active',b.dataset.layer===layer);b.setAttribute('aria-pressed',String(b.dataset.layer===layer));});$('scene-layer').textContent=layerNames[layer];$('legend-title').textContent=layer==='risk'?'COMPOSITE RISK CLASSES':layer.toUpperCase()+' · INDEX 0–1';$('ramp').style.background=`linear-gradient(90deg,${palettes[layer].join(',')})`;document.querySelector('.legend-labels').firstElementChild.textContent=layer==='risk'?'Very low':'0';document.querySelector('.legend-labels').lastElementChild.textContent=layer==='risk'?'Very high':'1';faceColors=faces.map(faceColor);$('inspect').hidden=true;draw();}
  document.querySelectorAll('[data-layer]').forEach(b=>b.addEventListener('click',()=>selectLayer(b.dataset.layer)));
  for(const key of ['opacity','elevation'])$(key).addEventListener('input',()=>{state[key]=Number($(key).value)/100;$(key+'-value').textContent=key==='opacity'?`${Math.round(state[key]*100)}%`:`${state[key].toFixed(1)}×`;draw();});
  for(const key of ['markers','routes','wireframe','hotspots','satellite'])$(key).addEventListener('change',()=>{state[key]=$(key).checked;draw();});
  function setView(flat){state.flat=flat;$('view2d').classList.toggle('selected',flat);$('view3d').classList.toggle('selected',!flat);$('inspect').hidden=true;draw();}
  $('view2d').addEventListener('click',()=>setView(true));$('view3d').addEventListener('click',()=>setView(false));
  function zoom(delta){state.zoom=clamp(state.zoom+delta,.6,8);$('inspect').hidden=true;draw();}
  $('zoom-in').addEventListener('click',()=>zoom(.2));$('zoom-out').addEventListener('click',()=>zoom(-.2));
  $('reset').addEventListener('click',()=>{state.yaw=-.18;state.pitch=.8;state.zoom=1;state.cx=0;state.cz=0;setView(false);});
  canvas.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?-.12:.12);},{passive:false});
  function inspectGeo(x,z){
    const i=index(x,z),value=sample(state.layer,x,z);if(value===null||!classes[i]){$('inspect').hidden=true;return;}
    const geo=toGeo(x,z);$('inspect-source').textContent='SAVED 100 M RASTER';
    const cls=state.layer==='risk'?classes[i]:(state.layer==='landslide'?(data.validation?.quantile_breaks||[]).filter(b=>value>=b).length+1:null);
    $('inspect-class').textContent=cls?names[cls-1]:layerNames[state.layer];
    $('inspect-value').textContent=`Index ${value.toFixed(3)} · elevation ${Math.round(metersAt(x,z))} m`;
    $('inspect-coords').textContent=`${geo.lat.toFixed(5)}° N, ${geo.lon.toFixed(5)}° E`;$('inspect').hidden=false;
  }
  function inspectPoint(x,y){
    const marker=markerPositions.find(m=>Math.hypot(m.screen.x-x,m.screen.y-y)<9);if(marker){inspectGeo(marker.x,marker.z);return;}
    for(let i=triangles.length-1;i>=0;i--){const f=triangles[i].f,[a,b,c]=f.ids.map(k=>projected[k]),den=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y);if(Math.abs(den)<.001)continue;const u=((b.y-c.y)*(x-c.x)+(c.x-b.x)*(y-c.y))/den,v=((c.y-a.y)*(x-c.x)+(a.x-c.x)*(y-c.y))/den;if(u>=0&&v>=0&&u+v<=1){const [va,vb,vc]=f.ids.map(k=>vertices[k]);inspectGeo(va.x*u+vb.x*v+vc.x*(1-u-v),va.z*u+vb.z*v+vc.z*(1-u-v));return;}}
    $('inspect').hidden=true;
  }
  let drag=null;
  canvas.addEventListener('pointerdown',e=>{if(!e.isPrimary)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY,sx:e.clientX,sy:e.clientY,moved:false,pan:e.shiftKey||e.button===2};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.moved ||= Math.hypot(e.clientX-drag.sx,e.clientY-drag.sy)>4;if(drag.pan){const scale=baseScale()*state.zoom;state.cx-=(dx*Math.cos(state.yaw)+dy*Math.sin(state.yaw))/scale;state.cz-=(dy*Math.cos(state.yaw)-dx*Math.sin(state.yaw))/scale;}else{state.yaw+=dx*.006;if(!state.flat)state.pitch=clamp(state.pitch+dy*.004,.25,1.35);}drag.x=e.clientX;drag.y=e.clientY;$('inspect').hidden=true;draw();});
  canvas.addEventListener('pointerup',e=>{if(!drag||drag.id!==e.pointerId)return;const click=!drag.moved;drag=null;if(click){const r=canvas.getBoundingClientRect();inspectPoint(e.clientX-r.left,e.clientY-r.top);}});
  canvas.addEventListener('pointercancel',()=>{drag=null;});canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Escape'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')state.yaw-=.08;if(e.key==='ArrowRight')state.yaw+=.08;if(e.key==='ArrowUp')state.pitch=clamp(state.pitch+.06,.25,1.35);if(e.key==='ArrowDown')state.pitch=clamp(state.pitch-.06,.25,1.35);if(e.key==='+'||e.key==='=')zoom(.2);if(e.key==='-')zoom(-.2);if(e.key==='Escape')$('inspect').hidden=true;draw();});
  $('close-inspect').addEventListener('click',()=>{$('inspect').hidden=true;});
  $('focus-event').addEventListener('click',()=>{if(!eventMarker)return;selectLayer('landslide');state.cx=eventMarker.x;state.cz=eventMarker.z;state.zoom=2.8;state.pitch=.75;state.markers=true;$('markers').checked=true;setView(false);$('inspect-source').textContent='SAVED EVENT VALIDATION';$('inspect-class').textContent=event.point_class_name;$('inspect-value').textContent=`Landslide index ${event.point_index.toFixed(3)}`;$('inspect-coords').textContent=`${event.lat}° N, ${event.lon}° E · 2 km validation zone`;$('inspect').hidden=false;});
  origins.forEach(([id,name])=>{const option=document.createElement('option');option.value=id;option.textContent=name;$('origin').append(option);});
  if(!origins.some(([id])=>id===state.origin))state.origin=origins[0]?.[0];$('origin').value=state.origin;
  function updateRoute(){
    const r=selectedRow();if(!r){$('destination-name').textContent='No route available for this selection.';for(const id of ['safe-distance','short-distance','safe-risk','short-risk','route-reduction','route-increase','route-note'])$(id).textContent='—';draw();return;}
    $('destination-name').textContent=r.destination;$('safe-distance').textContent=`${Number(r.least_risk_km).toFixed(2)} km`;$('short-distance').textContent=`${Number(r.shortest_km).toFixed(2)} km`;
    $('safe-risk').textContent=`Mean risk ${Number(r.least_risk_mean_risk).toFixed(3)} · max ${Number(r.least_risk_max_risk).toFixed(3)}`;
    $('short-risk').textContent=`Mean risk ${Number(r.shortest_mean_risk).toFixed(3)} · max ${Number(r.shortest_max_risk).toFixed(3)}`;
    const same=String(r.same_route).toLowerCase()==='true';$('route-reduction').textContent=same?'Same path for both objectives':`${Number(r.mean_risk_reduction_pct).toFixed(2)}% lower mean risk`;
    $('route-increase').textContent=`${Number(r.distance_increase_pct).toFixed(2)}% more distance`;
    $('route-note').textContent=same?'The saved model found no alternative with a lower weighted cost for this pair. The routes overlap.':'Solid green is the least-risk route; dashed cream is the shortest route. These are saved OSM network paths.';draw();
  }
  $('origin').addEventListener('change',()=>{state.origin=Number($('origin').value);updateRoute();});$('destination').addEventListener('change',()=>{state.destination=$('destination').value;updateRoute();});
  $('focus-route').addEventListener('click',()=>{const coords=selectedRoutes().flatMap(f=>f.geometry.coordinates);if(!coords.length)return;const points=coords.map(toWorld),xs=points.map(p=>p.x),zs=points.map(p=>p.z),dx=Math.max(...xs)-Math.min(...xs),dz=Math.max(...zs)-Math.min(...zs);state.cx=(Math.max(...xs)+Math.min(...xs))/2;state.cz=(Math.max(...zs)+Math.min(...zs))/2;state.zoom=clamp(1.1/Math.max(dx,dz,.16),1.5,6);state.routes=true;$('routes').checked=true;setView(false);});
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>{const tab=b.dataset.tab;document.querySelectorAll('[data-tab]').forEach(button=>{button.classList.toggle('active',button===b);button.setAttribute('aria-pressed',String(button===b));});for(const name of ['overview','routing','methods'])$(name+'-panel').hidden=name!==tab;if(tab==='routing'){state.routes=true;$('routes').checked=true;draw();}}));
  $('export').addEventListener('click',()=>{render();ctx.save();ctx.fillStyle='#102219e6';ctx.fillRect(0,height-34,width,34);ctx.fillStyle='#dae8cf';ctx.font='9px "Segoe UI",sans-serif';ctx.fillText(`Wayanad · ${layerNames[state.layer]} · SRTM / Sentinel-2 / © OSM · historical model`,12,height-13);ctx.restore();const anchor=document.createElement('a');anchor.download=`wayanad-${state.layer}-geographic.png`;anchor.href=canvas.toDataURL('image/png');anchor.click();draw();});
  $('ramp').style.background=`linear-gradient(90deg,${colors.join(',')})`;
  if(!data.raster.elevation){state.elevation=0;$('elevation').value=0;$('elevation-value').textContent='Unavailable';}
  if(!data.texture){state.satellite=false;$('satellite').checked=false;$('satellite').disabled=true;}
  updateRoute();resize();
})();
