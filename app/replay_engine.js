/* HAUNT Replay Engine — cinematic canvas round replay.
 * 60fps, pooled particles, zero per-frame allocation in the hot loop.
 * Phases: INTRO → HAUNT → REVEAL → AFTERMATH. (Tension is audio-only: filter sweep.)
 */
(function(){
'use strict';

// ---------- utils ----------
const clamp=(v,a,b)=>v<a?a:v>b?b:v;
// thermal heat scale: cold blue -> blue -> green -> amber -> red hot
const HEAT_STOPS=[[10,16,32],[30,64,175],[16,185,129],[245,158,11],[239,68,68]];

const lerp=(a,b,t)=>a+(b-a)*t;
const easeOut=t=>1-Math.pow(1-t,3);
const easeInOut=t=>t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;

// ---------- audio ----------
const Audio_ = {
  ctx:null, master:null, droneGain:null, droneFilter:null, muted:false, lastBlip:0,
  ensure(){
    if(this.ctx) { if(this.ctx.state==='suspended') this.ctx.resume(); return; }
    const C=window.AudioContext||window.webkitAudioContext; if(!C) return;
    this.ctx=new C(); this.master=this.ctx.createGain();
    this.master.gain.value=this.muted?0:0.9; this.master.connect(this.ctx.destination);
    // drone: two detuned sines through a lowpass
    this.droneFilter=this.ctx.createBiquadFilter(); this.droneFilter.type='lowpass';
    this.droneFilter.frequency.value=240; this.droneGain=this.ctx.createGain();
    this.droneGain.gain.value=0.035;
    const o1=this.ctx.createOscillator(),o2=this.ctx.createOscillator();
    o1.frequency.value=55; o2.frequency.value=55.7; o1.type=o2.type='sine';
    o1.connect(this.droneFilter); o2.connect(this.droneFilter);
    this.droneFilter.connect(this.droneGain); this.droneGain.connect(this.master);
    o1.start(); o2.start();
  },
  setTension(x){ // 0..1
    if(!this.ctx||!this.droneFilter)return;
    this.droneFilter.frequency.setTargetAtTime(240+x*1400,this.ctx.currentTime,.4);
    this.droneGain.gain.setTargetAtTime(.035+x*.05,this.ctx.currentTime,.4);
  },
  blip(amount){ // deploy chime, pitch by amount
    if(!this.ctx||this.muted)return;
    const now=performance.now(); if(now-this.lastBlip<70)return; this.lastBlip=now;
    const t=this.ctx.currentTime, o=this.ctx.createOscillator(), g=this.ctx.createGain();
    const f=520+140*Math.log10(1+amount*800);
    o.type='triangle'; o.frequency.value=f;
    g.gain.setValueAtTime(.09,t); g.gain.exponentialRampToValueAtTime(.0008,t+.18);
    o.connect(g); g.connect(this.master); o.start(t); o.stop(t+.2);
  },
  whale(){
    if(!this.ctx||this.muted)return;
    const _wn=performance.now(); if(_wn-(this._lastWhale||0)<300)return; this._lastWhale=_wn;
    const t=this.ctx.currentTime, o=this.ctx.createOscillator(), g=this.ctx.createGain();
    o.type='sine'; o.frequency.setValueAtTime(130,t); o.frequency.exponentialRampToValueAtTime(38,t+.45);
    g.gain.setValueAtTime(.22,t); g.gain.exponentialRampToValueAtTime(.0008,t+.5);
    o.connect(g); g.connect(this.master); o.start(t); o.stop(t+.55);
  },
  winner(){
    if(!this.ctx||this.muted)return;
    const notes=[220,261.63,293.66,329.63,392,440,523.25];
    notes.forEach((f,i)=>{
      const t=this.ctx.currentTime+i*.13, o=this.ctx.createOscillator(), g=this.ctx.createGain();
      o.type='triangle'; o.frequency.value=f;
      g.gain.setValueAtTime(0,t); g.gain.linearRampToValueAtTime(.12,t+.02);
      g.gain.exponentialRampToValueAtTime(.0008,t+.9);
      o.connect(g); g.connect(this.master); o.start(t); o.stop(t+1);
    });
  },
  toggleMute(){ this.muted=!this.muted;
    if(this.master) this.master.gain.value=this.muted?0:0.9; return this.muted; }
};

// ---------- particle pool ----------
// particle pool is per-engine (see constructor)

// ---------- engine ----------
class Engine{
  constructor(canvas, round, opts){
    this.silent=opts&&opts.silent;
    this.MAXP=420; this.pool=[]; for(let i=0;i<this.MAXP;i++) this.pool.push({on:false}); this._ring=0;
    this.cv=canvas; this.ctx=canvas.getContext('2d');
    this.round=round;
    if(!Number.isInteger(round.winner)||round.winner<0||round.winner>24) throw new Error('bad winner '+round.winner);
    // normalize timeline: ms offsets, stagger same-second events
    const evs=round.timeline.map(e=>({...e, ms:Date.parse(e.t)})).filter(e=>Number.isFinite(e.ms)&&Number.isFinite(e.s)&&e.q&&e.q.length&&e.q.every(q=>Number.isInteger(q)&&q>=0&&q<25));
    if(!evs.length) throw new Error('no valid events');
    let t0=Infinity; for(const e of evs) if(e.ms<t0) t0=e.ms;
    const perSec={};
    evs.forEach(e=>{ const s=Math.floor((e.ms-t0)/1000); perSec[s]=perSec[s]||[]; perSec[s].push(e); });
    Object.values(perSec).forEach(arr=>arr.forEach((e,i)=>{ e.off=(Math.floor((e.ms-t0)/1000)*1000)+ (i/arr.length)*1000; }));
    evs.sort((a,b)=>a.off-b.off);
    this.evs=evs; this.dur=Math.max(1000, evs[evs.length-1].off+1500); this.t0=t0;
    this.tileSol=new Float64Array(25); this.tileFlash=new Float64Array(25);
    this.totalSol=0; this.deploysShown=0; this.evIdx=0;
    this.t=0; this.playing=false; this.speed=2; this.phase='intro';
    this.phaseT=0; this.shockwaves=[]; this.leader=0;
    this.whaleAt=evs.map(e=>e.s/Math.max(1,e.q.length)).sort((a,b)=>a-b)[Math.floor(evs.length*0.95)]||0;
    this.resize();
  }
  resize(){
    const r=this.cv.parentElement.getBoundingClientRect();
    const dpr=Math.min(2,window.devicePixelRatio||1);
    this.w=r.width; this.h=Math.min(Math.round(r.width*1.12), 500); // DESIGN-OVERHAUL: taller cinematic frame
    this.cv.width=this.w*dpr; this.cv.height=this.h*dpr;
    this.cv.style.width=this.w+'px'; this.cv.style.height=this.h+'px';
    this.ctx.setTransform(dpr,0,0,dpr,0,0);
    const pad=14, gap=7, n=5;
    this.cell=(Math.min(this.w,this.h)-pad*2-gap*(n-1))/n;
   
    this.ox=(this.w-(this.cell*n+gap*(n-1)))/2; this.oy=pad+6;
  }
  tileXY(i){ const c=i%5,r=(i/5)|0;
    return [this.ox+c*(this.cell+7), this.oy+r*(this.cell+7)]; }
  tileCX(i){ const [x,y]=this.tileXY(i); return [x+this.cell/2, y+this.cell/2]; }
  reset(){ this.afterT=0; this.tileSol.fill(0); this.tileFlash.fill(0); this.totalSol=0;
    this.deploysShown=0; this.evIdx=0; this.t=0; this.shockwaves.length=0;
    this.pool.forEach(p=>p.on=false); this.phase='intro'; this.phaseT=0;
    this.leader=0; }
  spawn(x,y,vx,vy,life,size,hue,alpha,grav){
    let p=null;
    for(let k=0;k<this.MAXP;k++){ const i=(this._ring+k)%this.MAXP; if(!this.pool[i].on){ p=this.pool[i]; this._ring=(i+1)%this.MAXP; break; } }
    if(!p){ p=this.pool[this._ring]; this._ring=(this._ring+1)%this.MAXP; }
    p.on=true;p.x=x;p.y=y;p.vx=vx;p.vy=vy;p.life=life;p.maxLife=life;p.delay=0;
    p.size=size;p.hue=hue;p.alpha=alpha;p.grav=grav||0;
    p.wisp=false;p.sx=0;p.sy=0;p.tx=0;p.ty=0;
    return p;
  }
  play(){ if(!this.silent) Audio_.ensure(); this.playing=true; if(this.phase==='after') this.reset(), this.playing=true; }
  pause(){ this.playing=false; }
  seek(frac){ // 0..1 — rebuild state deterministically
    this.reset();
    const target=frac*this.dur;
    while(this.evIdx<this.evs.length && this.evs[this.evIdx].off<=target){ this.apply(this.evs[this.evIdx],true); this.evIdx++; }
    this.t=target; this.deploysShown=this.evIdx;
    if(frac>=1){ this.doReveal(true); this.phase='reveal'; this.phaseT=0; }
    else this.phase = frac>0.02?'haunt':'intro';
  }
  apply(e,silent){
    const per=e.s/Math.max(1,e.q.length);
    e.q.forEach(q=>{ this.tileSol[q]+=per; if(!silent) this.tileFlash[q]=1; });
    this.totalSol+=e.s; this.deploysShown++;
    let _mx=0,_mi=0; for(let _i=0;_i<25;_i++) if(this.tileSol[_i]>_mx){_mx=this.tileSol[_i];_mi=_i;}
    this.leader=_mi;
    if(!silent){
      const whale=(e.s/Math.max(1,e.q.length))>=this.whaleAt;
      e.q.forEach(q=>{
        const [cx,cy]=this.tileCX(q);
        const n=whale?5:2;
        for(let k=0;k<n;k++){
          const a=Math.random()*Math.PI*2, sp=20+Math.random()*60;
          this.spawn(cx+(Math.random()-.5)*this.cell*.5, cy+(Math.random()-.5)*this.cell*.5,
            Math.cos(a)*sp, Math.sin(a)*sp-30, .5+Math.random()*.4,
            whale?3.4:2.2, whale?45:160, .9, 60);
        }
        // incoming wisp from edge
        const ex=Math.random()<.5?(Math.random()<.5?-20:this.w+20):Math.random()*this.w;
        const ey=ex<0||ex>this.w?Math.random()*this.h:(Math.random()<.5?-20:this.h+20);
        const w=this.spawn(ex,ey,0,0,.7,2.6,160,.85,0); w.tx=cx; w.ty=cy; w.wisp=true; w.sx=ex; w.sy=ey;
      });
      if(!this.silent){ if(whale) Audio_.whale(); else Audio_.blip(e.s); }
    }
  }
  update(dt){
    // dt seconds
    if(this.playing && this.phase!=='reveal' && this.phase!=='after'){
      this.t+=dt*1000*this.speed;
      while(this.evIdx<this.evs.length && this.evs[this.evIdx].off<=this.t){
        this.apply(this.evs[this.evIdx],false); this.evIdx++;
      }
      const p=this.t/this.dur;
      if(!this.silent) Audio_.setTension(clamp((p-.7)/.3,0,1));
      if(p>=1){ this.phase='reveal'; this.phaseT=0; this.doReveal(); }
      else if(p>0.02&&this.phase==='intro'){this.phase='haunt';}
    }
    if(this.phase==='reveal'){
      this.phaseT+=dt;
      if(this.phaseT>2.6){ this.phase='after'; this.afterT=0; if(this.onAfter) this.onAfter(); }
    }
    if(this.phase==='after'){
      this.afterT=(this.afterT||0)+dt;
      if(this.afterT>4){ this.reset(); this.playing=true; if(this.onReset) this.onReset(); }
    }
    // decay flashes
    for(let i=0;i<25;i++) if(this.tileFlash[i]>0) this.tileFlash[i]=Math.max(0,this.tileFlash[i]-dt*2.2);
    // particles
    for(let i=0;i<this.MAXP;i++){ const p=this.pool[i]; if(!p.on)continue;
      if(p.delay>0){ p.delay-=dt; continue; }
      p.life-=dt;
      if(p.life<=0){p.on=false;continue;}
      if(p.wisp){
        const k=1-Math.pow(1-clamp(1-p.life/p.maxLife,0,1),2);
        p.x=lerp(p.sx,p.tx,easeInOut(k)); p.y=lerp(p.sy,p.ty,easeInOut(k));
      }else{
        p.vy+=(p.grav||0)*dt; p.x+=p.vx*dt; p.y+=p.vy*dt;
      }
    }
    // shockwaves
    for(let i=this.shockwaves.length-1;i>=0;i--){ const s=this.shockwaves[i];
      s.r+=dt*260; s.a-=dt*.8; if(s.a<=0)this.shockwaves.splice(i,1); }
  }
  doReveal(silent){
    if(!this.silent){ if(!silent) Audio_.winner(); Audio_.setTension(0); }
    const [cx,cy]=this.tileCX(this.round.winner);
    for(let k=0;k<3;k++) this.shockwaves.push({x:cx,y:cy,r:10+k*8,a:1});
    for(let k=0;k<90;k++){
      const a=Math.random()*Math.PI*2, sp=40+Math.random()*160;
      const p=this.spawn(cx,cy,Math.cos(a)*sp,Math.sin(a)*sp-60,1+Math.random()*1.2,2.5+Math.random()*2.5,45,.95,120);
      if(p) p.delay=Math.random()*0.6; // fountain, not flash
    }
  }
  draw(){
    const c=this.ctx; c.clearRect(0,0,this.w,this.h);
    let mx=.0001; for(let k=0;k<25;k++){ const _v=this.tileSol[k]; if(_v>mx) mx=_v; }
    const dimmed=this.phase==='reveal'||this.phase==='after';
    const cellStep=this.cell+7, ox=this.ox, oy=this.oy, cell=this.cell;
    const thumbFont=`700 ${Math.max(11,cell*.28)}px system-ui`;
    const valFont=`700 ${Math.max(9,cell*.16)}px system-ui`;
    const idxFont=`${Math.max(8,cell*.11)}px system-ui`;
    for(let i=0;i<25;i++){
      const x=ox+(i%5)*cellStep, y=oy+((i/5)|0)*cellStep;
      const heat=this.tileSol[i]/mx;
      const isWin=i===this.round.winner;
      let r,g,b;
      if(dimmed&&!isWin){ r=14;g=14;b=26; }
      else{ const hh=heat<0?0:heat>1?1:heat, f=hh*4, ii=f>=4?3:(f|0), t=f-ii;
        const A=HEAT_STOPS[ii], B=HEAT_STOPS[ii+1];
        r=(A[0]+(B[0]-A[0])*t)|0; g=(A[1]+(B[1]-A[1])*t)|0; b=(A[2]+(B[2]-A[2])*t)|0; }
      const fl=this.tileFlash[i];
      c.fillStyle=`rgb(${r+fl*90|0},${g+fl*120|0},${b+fl*60|0})`;
      const rad=9;
      c.beginPath(); c.roundRect(x,y,cell,cell,rad); c.fill();
      if(isWin&&(this.phase==='reveal'||this.phase==='after')){
        c.strokeStyle=`rgba(255,176,0,${.6+.4*Math.sin(performance.now()/180)})`;
        c.lineWidth=3; c.beginPath(); c.roundRect(x-2,y-2,this.cell+4,this.cell+4,11); c.stroke();
      }
      // label — adaptive contrast; simplified on small thumbnails for legibility
      const brightTile=heat>0.62;
      const isThumb=cell<32; // thumbnail vs full player
      const v=this.tileSol[i];
      // DESIGN-OVERHAUL FIX: labels must draw EVERY frame — draw() clearRect()s the canvas
      // each frame, so the old dirty-check left values visible for a single frame then blank.
      // 25 fillTexts/frame is negligible; correctness beats the micro-optimization.
      const lblTxt=v>=10?v.toFixed(0):(isThumb?v.toFixed(1):v.toFixed(2));
      c.fillStyle=(dimmed&&!isWin)?'#6a6a8a':(brightTile?'#0a1420':'#d8f5e8');
      c.textAlign='center';
      c.font=isThumb?thumbFont:valFont;
      c.strokeStyle='rgba(0,0,0,.5)'; c.lineWidth=2.5;
      c.strokeText(lblTxt, x+cell/2, y+cell/2+(isThumb?cell*.1:4));
      c.fillText(lblTxt, x+cell/2, y+cell/2+(isThumb?cell*.1:4));
      if(!isThumb){
        c.font=idxFont; c.textAlign='left';
        c.fillStyle=(dimmed&&!isWin)?'#4a4a6a':'rgba(216,216,240,.55)';
        c.fillText(i, x+5, y+12);
      }
    }
    // shockwaves
    for(let si=0;si<this.shockwaves.length;si++){ const s=this.shockwaves[si];
      c.strokeStyle=`rgba(255,176,0,${s.a<0?0:s.a>1?1:s.a})`; c.lineWidth=3;
      c.beginPath(); c.arc(s.x,s.y,s.r,0,Math.PI*2); c.stroke();
    }
    // particles
    for(let i=0;i<this.MAXP;i++){ const p=this.pool[i]; if(!p.on)continue;
      const a=p.alpha*clamp(p.life/p.maxLife,0,1);
      c.fillStyle=`hsla(${p.hue},100%,${p.wisp?70:60}%,${a})`;
      c.beginPath(); c.arc(p.x,p.y,p.size,0,Math.PI*2); c.fill();
    }
    // intro title card
    if(this.phase==='intro'){
      c.fillStyle='rgba(5,5,12,.72)'; c.fillRect(0,0,this.w,this.h);
      c.textAlign='center'; c.fillStyle='#d8d8f0';
      const _th=cell<32;
      c.font=_th?`700 ${Math.max(13,cell*.5)}px system-ui`:'700 26px system-ui';
      try{ c.letterSpacing='3px'; }catch(e){}
      c.fillText('ROUND #'+this.round.round, this.w/2, this.h/2-(_th?14:26));
      try{ c.letterSpacing='0px'; }catch(e){}
      c.fillStyle='#8a8ab0'; c.font=_th?`${Math.max(9,cell*.2)}px system-ui`:'13px system-ui';
      c.fillText(this.evs.length+' deploys', this.w/2, this.h/2+(_th?4:6));
    }
  }
}

window.HauntReplay={Engine,Audio:Audio_};
})();
