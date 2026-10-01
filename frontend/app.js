/* Dependency-free, projected 3D terrain draft. All scene geometry is illustrative. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const data = window.OBSERVATORY_DATA || {stats: [], weights: [], validation: null};
  const colors = ['#708e59', '#a8be73', '#dbc16d', '#de975f', '#bf6256'];
  const names = ['Very low', 'Low', 'Moderate', 'High', 'Very high'];
  const layerNames = {risk: 'Composite multi-hazard risk', flood: 'Flood susceptibility', landslide: 'Landslide susceptibility', fire: 'Fire susceptibility', exposure: 'Settlement exposure'};
  const palettes = {risk: colors, flood: ['#536d59','#779b8e','#91c3bb','#77a8c5','#5083a7'], landslide: ['#637654','#a0ab68','#d0be7e','#d69a68','#b97052'], fire: ['#637651','#a7ad5a','#dcc06a','#df9457','#bd5950'], exposure: ['#5a715d','#829078','#a7a79d','#aaa0bb','#a98ccc']};
  const state = {layer:'risk', opacity:.7, elevation:1.4, yaw:-.32, pitch:.74, zoom:1, flat:false, markers:true, routes:true, wireframe:false, origin:0};
  const fmt = n => Number(n).toLocaleString('en-IN', {maximumFractionDigits:0});
  const total = data.stats.reduce((sum,r) => sum + Number(r.area_km2),0);
  const high = data.stats.filter(r=>Number(r.class_id)>=4).reduce((sum,r)=>sum+Number(r.area_km2),0);
  if (total) {
    $('area').textContent = fmt(total);
    $('high-risk').textContent = (high/total*100).toFixed(1);
    $('high-area').textContent = `${fmt(high)} km² in elevated risk classes`;
    $('risk-callout').textContent = `${(high/total*100).toFixed(1)}% in elevated risk classes`;
  } else $('risk-callout').textContent = 'No saved statistics available';
  data.stats.forEach((r,i)=>{
    const row = document.createElement('div'); row.className='distribution-row';
    const label = document.createElement('div'); label.className='distribution-label';
    const dot=document.createElement('span'); dot.className='dot'; dot.style.background=colors[i];
    const name=document.createElement('span'); name.textContent=r.class_name;
    const percent=document.createElement('span'); percent.className='percent'; percent.textContent=`${Number(r.percent_of_study_area).toFixed(1)}%`;
    label.append(dot,name,percent);
    const track=document.createElement('div'); track.className='bar-track';
    const bar=document.createElement('div'); bar.className='bar'; bar.style.width=`${r.percent_of_study_area}%`; bar.style.background=colors[i];track.append(bar);
    const area=document.createElement('small'); area.textContent=`${fmt(r.area_km2)} km²`;row.append(label,track,area);$('distribution').append(row);
  });
  const event=data.validation?.results?.find(r=>r.label==='verified');
  $('validation-status').textContent=data.validation ? (data.validation.headline_passed?'PASS':'FAIL'):'N/A';
  $('validation-share').textContent=event?`${(event.zone_fraction_high_or_very_high*100).toFixed(1)}% high risk`:'No saved result';
  $('validation-badge').textContent=data.validation ? (data.validation.headline_passed?'✓ VALIDATION PASSED':'VALIDATION FAILED'):'RESULT UNAVAILABLE';
  $('validation-detail').textContent=event?`${(event.zone_fraction_high_or_very_high*100).toFixed(1)}% of the 2 km event zone is classified High or Very High. Saved point class: ${event.point_class_name}.`:'Generate the validation output with the Python pipeline.';
  data.weights.forEach(r=>{const row=document.createElement('div');row.className='weight-row';const name=document.createElement('span');name.textContent=r.criterion[0].toUpperCase()+r.criterion.slice(1);const weight=document.createElement('span');weight.className='mint';weight.textContent=`${(Number(r['AHP+Entropy (used)'])*100).toFixed(1)}%`;row.append(name,weight);$('weights').append(row);});
  $('data-status').textContent=`${data.stats.length ? 'Saved statistics, weights and validation loaded.' : 'Saved statistics unavailable.'} ${data.availability?.routes ? 'Route output exists; the scene still uses illustrative paths.' : 'Computed route output is not available.'} Terrain is procedural, not an SRTM elevation model.`;

  const canvas=$('terrain');const ctx=canvas.getContext('2d');
  if (!ctx) {$('scene-layer').textContent='Canvas is unavailable in this browser.';return;}
  let width=1,height=1,pending=false,projected=[],triangles=[];
  const clamp=(v,min=0,max=1)=>Math.max(min,Math.min(max,v));
  const bump=(x,z,cx,cz,sx,sz,a)=>a*Math.exp(-((x-cx)**2/sx**2+(z-cz)**2/sz**2));
  function terrain(x,z){
    const ridge=bump(x,z,-.62,.08,.28,.8,.65)+bump(x,z,-.28,-.4,.35,.43,.57)+bump(x,z,.1,-.6,.29,.3,.4)+bump(x,z,.54,.13,.4,.55,.26);
    const texture=(Math.sin(x*27+z*19)+Math.sin(z*35-x*11))*.025+(Math.sin(x*12-z*8))*.035;
    return Math.max(.018,ridge+texture+.06);
  }
  function sample(layer,x,z){
    const h=terrain(x,z), ripple=(Math.sin(x*19+z*7)+Math.sin(z*23-x*11))*.04;
    const flood=clamp(.72-h*1.6+bump(x,z,.25,.23,.2,.65,.28)+ripple);
    const landslide=clamp(h*1.6+ripple);
    const fire=clamp(bump(x,z,.42,-.25,.55,.4,.8)+ripple+.08);
    const exposure=clamp(bump(x,z,.1,.3,.18,.3,.8)+bump(x,z,.55,-.08,.2,.2,.6)+ripple);
    return layer==='risk'?clamp((flood*.55+landslide*.16+fire*.16+exposure*.13)*1.5-.08):({flood,landslide,fire,exposure})[layer];
  }
  const vertices=[],faces=[];const nx=65,nz=53;
  for(let j=0;j<nz;j++)for(let i=0;i<nx;i++){const x=i/(nx-1)*2-1,z=j/(nz-1)*1.8-.9;vertices.push({x,z,h:terrain(x,z)});}
  function inside(x,z){return (x/.98)**2+(z/.89)**2 < 1+.1*Math.sin(z*8+x*4);}
  for(let j=0;j<nz-1;j++)for(let i=0;i<nx-1;i++){
    const a=j*nx+i,b=a+1,c=a+nx,d=c+1;
    for(const ids of [[a,c,b],[b,c,d]]){const x=ids.reduce((s,k)=>s+vertices[k].x,0)/3,z=ids.reduce((s,k)=>s+vertices[k].z,0)/3;if(inside(x,z))faces.push({ids,x,z,h:terrain(x,z)});}
  }
  function project(x,z,h=terrain(x,z)){
    const cy=Math.cos(state.yaw),sy=Math.sin(state.yaw),rx=x*cy-z*sy,rz=x*sy+z*cy;
    const p=state.flat?0:state.pitch,e=state.flat?0:h*state.elevation;
    const scale=Math.min(width*.43,height*.53)*state.zoom;
    return {x:width*.49+rx*scale,y:height*.58+(rz*Math.cos(p)-e*Math.sin(p))*scale,depth:rz*Math.sin(p)+e*Math.cos(p)};
  }
  function rgb(hex){return hex.match(/[a-f0-9]{2}/gi).map(x=>parseInt(x,16));}
  function shade(face){
    const value=sample(state.layer,face.x,face.z),palette=palettes[state.layer];
    const n=value*4,idx=Math.min(3,Math.floor(n)),t=n-idx;
    const a=rgb(palette[idx]),b=rgb(palette[idx+1]);
    const dx=(terrain(face.x+.015,face.z)-face.h)/.015,dz=(terrain(face.x,face.z+.015)-face.h)/.015;
    const light=clamp(.86+(-dx*.1+dz*.06),.45,1.15),base=[70,91,63];
    return `rgb(${a.map((v,k)=>Math.round(((v+(b[k]-v)*t)*state.opacity+base[k]*(1-state.opacity))*light)).join(',')})`;
  }
  function path(points,color,lineWidth,dashed=false){ctx.beginPath();points.forEach(([x,z],i)=>{const p=project(x,z,terrain(x,z)+.018);i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);});ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.lineJoin='round';ctx.lineCap='round';ctx.setLineDash(dashed?[5,5]:[]);ctx.stroke();ctx.setLineDash([]);}
  const origins=[[-.25,.55],[.1,.25],[.58,.04]],destination=[.15,-.25];
  function routes(){const [x,z]=origins[state.origin];return {short:[[x,z],[x+.08,z-.18],[.12,.03],destination],safe:[[x,z],[x+.23,z+.03],[.43,.33],[.48,.1],[.35,-.13],destination]};}
  const markers=[{x:-.25,z:.55,name:'Southern settlement',type:'origin'},{x:.1,z:.25,name:'Central settlement',type:'origin'},{x:.58,z:.04,name:'Eastern settlement',type:'origin'},{x:.15,z:-.25,name:'Hospital · demo',type:'hospital'},{x:-.55,z:.15,name:'Event zone · illustrative position',type:'event'}];
  function render(){
    pending=false;ctx.clearRect(0,0,width,height);
    // A quiet floor grid gives spatial depth without a network basemap.
    ctx.lineWidth=.5;ctx.strokeStyle='#b8cf9c12';
    for(let i=-1.6;i<=1.6;i+=.2){for(const points of [[[i,-1.4],[i,1.4]],[[-1.6,i],[1.6,i]]]){ctx.beginPath();points.forEach(([x,z],k)=>{const p=project(x,z,0);k?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);});ctx.stroke();}}
    const shadow=project(0,0,0);ctx.save();ctx.translate(shadow.x,shadow.y+15);ctx.scale(1,.35);const gradient=ctx.createRadialGradient(0,0,10,0,0,width*.4*state.zoom);gradient.addColorStop(0,'#06120c88');gradient.addColorStop(1,'#06120c00');ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(0,0,width*.4*state.zoom,0,Math.PI*2);ctx.fill();ctx.restore();
    projected=vertices.map(v=>project(v.x,v.z,v.h));
    triangles=faces.map(f=>({f,depth:f.ids.reduce((s,k)=>s+projected[k].depth,0)/3})).sort((a,b)=>a.depth-b.depth);
    for(const {f} of triangles){ctx.beginPath();f.ids.forEach((k,i)=>{const p=projected[k];i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);});ctx.closePath();ctx.fillStyle=shade(f);ctx.fill();ctx.strokeStyle=state.wireframe?'#d7e9c747':ctx.fillStyle;ctx.lineWidth=state.wireframe?.5:.35;ctx.stroke();}
    // Illustrative river through valley, separate from flood susceptibility.
    path([[.0,.8],[.1,.62],[.05,.47],[.15,.32],[.22,.13],[.17,-.04],[.24,-.2],[.26,-.42],[.2,-.64]],'#a3cfca8c',1.8);
    if(state.routes){const r=routes();path(r.short,'#e3dcc5b0',2,true);path(r.safe,'#c6ed9b',2.8);}
    if(state.markers){for(const m of markers){const p=project(m.x,m.z,terrain(m.x,m.z)+.02);ctx.beginPath();ctx.arc(p.x,p.y,4,0,Math.PI*2);ctx.fillStyle=m.type==='event'?'#eea270':'#d5ebbb';ctx.fill();ctx.strokeStyle='#23321d';ctx.lineWidth=1.5;ctx.stroke();ctx.font='9px "Segoe UI", sans-serif';const tw=ctx.measureText(m.name).width;ctx.fillStyle='#152019d9';ctx.fillRect(p.x+9,p.y-9,tw+12,19);ctx.fillStyle='#dce7d1';ctx.fillText(m.name,p.x+15,p.y+4);}}
    $('compass-arrow').style.transform=`rotate(${-state.yaw*180/Math.PI}deg)`;
    $('scene-state').textContent=state.flat?'2D / PLAN VIEW':`3D / ${state.elevation.toFixed(1)}× ELEVATION`;
  }
  function draw(){if(!pending){pending=true;requestAnimationFrame(render);}}
  function resize(){const bounds=canvas.getBoundingClientRect();width=bounds.width;height=bounds.height;const dpr=Math.min(window.devicePixelRatio||1,2);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);draw();}
  new ResizeObserver(resize).observe($('scene'));
  document.querySelectorAll('[data-layer]').forEach(button=>button.addEventListener('click',()=>{
    state.layer=button.dataset.layer;document.querySelectorAll('[data-layer]').forEach(b=>b.classList.toggle('active',b===button));
    $('scene-layer').textContent=layerNames[state.layer];$('legend-title').textContent=state.layer==='risk'?'COMPOSITE RISK':state.layer.toUpperCase();
    $('ramp').style.background=`linear-gradient(90deg,${palettes[state.layer].join(',')})`;$('inspect').hidden=true;draw();
  }));
  for(const key of ['opacity','elevation'])$(key).addEventListener('input',()=>{state[key]=Number($(key).value)/100;$(key+'-value').textContent=key==='opacity'?`${Math.round(state[key]*100)}%`:`${state[key].toFixed(1)}×`;draw();});
  for(const key of ['markers','routes','wireframe'])$(key).addEventListener('change',()=>{state[key]=$(key).checked;draw();});
  function setView(flat){state.flat=flat;$('view2d').classList.toggle('selected',flat);$('view3d').classList.toggle('selected',!flat);$('inspect').hidden=true;draw();}
  $('view2d').addEventListener('click',()=>setView(true));$('view3d').addEventListener('click',()=>setView(false));
  function zoom(delta){state.zoom=clamp(state.zoom+delta,.55,2.4);$('inspect').hidden=true;draw();}
  $('zoom-in').addEventListener('click',()=>zoom(.12));$('zoom-out').addEventListener('click',()=>zoom(-.12));
  $('reset').addEventListener('click',()=>{state.yaw=-.32;state.pitch=.74;state.zoom=1;setView(false);});
  canvas.addEventListener('wheel',e=>{e.preventDefault();zoom(e.deltaY>0?-.07:.07);},{passive:false});
  let drag=null;
  canvas.addEventListener('pointerdown',e=>{if(!e.isPrimary)return;drag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag||drag.id!==e.pointerId)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;drag.moved ||= Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>4;state.yaw+=dx*.007;if(!state.flat)state.pitch=clamp(state.pitch+dy*.004,.2,1.25);drag.x=e.clientX;drag.y=e.clientY;$('inspect').hidden=true;draw();});
  function inspectPoint(x,y){
    // Search visible triangles from front to back to respect terrain occlusion.
    for(let i=triangles.length-1;i>=0;i--){const f=triangles[i].f,[a,b,c]=f.ids.map(k=>projected[k]);const den=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y);if(Math.abs(den)<.001)continue;const u=((b.y-c.y)*(x-c.x)+(c.x-b.x)*(y-c.y))/den,v=((c.y-a.y)*(x-c.x)+(a.x-c.x)*(y-c.y))/den;if(u>=0&&v>=0&&u+v<=1){const value=sample(state.layer,f.x,f.z);$('inspect-class').textContent=`${names[Math.min(4,Math.floor(value*5))]} · demo`;$('inspect-value').textContent=`${layerNames[state.layer]}: ${value.toFixed(3)}`;$('inspect-coords').textContent=`Scene coordinates ${f.x.toFixed(2)}, ${f.z.toFixed(2)} · not geographic`;$('inspect').hidden=false;return;}}
    $('inspect').hidden=true;
  }
  canvas.addEventListener('pointerup',e=>{if(!drag||drag.id!==e.pointerId)return;const click=!drag.moved;drag=null;if(click){const r=canvas.getBoundingClientRect();inspectPoint(e.clientX-r.left,e.clientY-r.top);}});
  canvas.addEventListener('pointercancel',()=>{drag=null;});
  canvas.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Escape'].includes(e.key))e.preventDefault();if(e.key==='ArrowLeft')state.yaw-=.08;if(e.key==='ArrowRight')state.yaw+=.08;if(e.key==='ArrowUp')state.pitch=clamp(state.pitch+.06,.2,1.25);if(e.key==='ArrowDown')state.pitch=clamp(state.pitch-.06,.2,1.25);if(e.key==='+'||e.key==='=')zoom(.12);if(e.key==='-')zoom(-.12);if(e.key==='Escape')$('inspect').hidden=true;draw();});
  $('close-inspect').addEventListener('click',()=>{$('inspect').hidden=true;});
  $('focus-event').addEventListener('click',()=>{state.yaw=-.32;state.pitch=.9;state.zoom=1.15;state.markers=true;$('markers').checked=true;setView(false);$('inspect-class').textContent=event?`${event.point_class_name} · saved result`:'Event result unavailable';$('inspect-value').textContent=event?`Landslide index: ${event.point_index.toFixed(3)}`:'Run the Python validation pipeline.';$('inspect-coords').textContent=event?`${event.lat}° N, ${event.lon}° E · scene position illustrative`:'Illustrative scene marker';$('inspect').hidden=false;});
  const examples=[{safe:8.6,short:7.2,safeRisk:.21,shortRisk:.38},{safe:6.1,short:5.4,safeRisk:.18,shortRisk:.31},{safe:9.3,short:8.8,safeRisk:.24,shortRisk:.35}];
  function updateRoute(){const r=examples[state.origin];$('safe-distance').textContent=`${r.safe.toFixed(1)} km`;$('short-distance').textContent=`${r.short.toFixed(1)} km`;$('safe-risk').textContent=`Mean risk ${r.safeRisk.toFixed(2)} · illustrative`;$('short-risk').textContent=`Mean risk ${r.shortRisk.toFixed(2)} · illustrative`;$('route-reduction').textContent=`${((1-r.safeRisk/r.shortRisk)*100).toFixed(1)}% lower mean risk`;$('route-increase').textContent=`${((r.safe/r.short-1)*100).toFixed(1)}% longer distance · demo comparison`;draw();}
  $('origin').addEventListener('change',()=>{state.origin=Number($('origin').value);updateRoute();});updateRoute();
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{const tab=button.dataset.tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',b===button?'true':'false');});for(const key of ['overview','routing','methods'])$(key+'-panel').hidden=key!==tab;if(tab==='routing'){state.routes=true;$('routes').checked=true;draw();}}));
  $('export').addEventListener('click',()=>{render();const anchor=document.createElement('a');anchor.download=`wayanad-${state.layer}-illustrative.png`;anchor.href=canvas.toDataURL('image/png');anchor.click();});
  resize();
})();
