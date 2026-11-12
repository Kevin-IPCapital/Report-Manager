/** Dependency-free, locally-generated multi-page PDF. No data is sent to third-party services. */
const PAGE_W = 595.28, PAGE_H = 841.89, LEFT = 44, RIGHT = 551;
const clean = (value) => String(value ?? '—').replace(/[\r\n\t]+/g, ' ').replace(/[^\x20-\x7e\xa0-\xff]/g, '?');
const esc = (s) => clean(s).replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
const bytes = (s) => new Uint8Array([...s].map((c) => c.charCodeAt(0) & 255));
const wrap = (text, max) => {
  const parts = clean(text).split(/\s+/), lines = []; let line = '';
  for (const w of parts) {
    if ((line + ' ' + w).trim().length > max && line) { lines.push(line); line = ''; }
    if (w.length > max) { if(line) { lines.push(line); line = ''; } for(let i=0;i<w.length;i+=max) lines.push(w.slice(i,i+max)); }
    else line = (line + ' ' + w).trim();
  }
  if (line) lines.push(line);
  return lines.length ? lines : ['—'];
};
const TOC_ROWS_PER_PAGE = 30;
export function buildPdf({ title, subtitle, period, sections, annex = [], generatedAt = new Date() }) {
  const pages = []; let cmds = [], y = 0, pageIndex = 0;
  const cmd = (s) => cmds.push(s);
  const textCmd = (s, x, top, size=10, bold=false, shade='0.13 0.18 0.29') => `${shade} rg BT /${bold ? 'F2':'F1'} ${size} Tf 1 0 0 1 ${x.toFixed(2)} ${(PAGE_H-top).toFixed(2)} Tm (${esc(s)}) Tj ET`;
  const text = (...args) => cmd(textCmd(...args));
  const palette = ['0.17 0.24 0.54', '0.89 0.12 0.17', '0.19 0.49 0.80', '0.93 0.55 0.20', '0.28 0.61 0.51', '0.49 0.36 0.68', '0.48 0.56 0.67', '0.71 0.35 0.44'];
  const rect = (x, top, w, h, color) => cmd(`${color} rg ${x.toFixed(2)} ${(PAGE_H-top-h).toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f`);
  const circle = (cx, cy, r, fill) => {
    const py = PAGE_H-cy, k = 0.55228475*r;
    cmd(`${fill} rg ${cx+r} ${py} m ${cx+r} ${py+k} ${cx+k} ${py+r} ${cx} ${py+r} c ${cx-k} ${py+r} ${cx-r} ${py+k} ${cx-r} ${py} c ${cx-r} ${py-k} ${cx-k} ${py-r} ${cx} ${py-r} c ${cx+k} ${py-r} ${cx+r} ${py-k} ${cx+r} ${py} c f`);
  };
  const donut = (item) => {
    const source = (item.data || []).filter(a=>Number(a.value)>0 && Number.isFinite(Number(a.value)));
    const rows = source.length>12 ? [...source.slice(0,11), {label:'Other',value:source.slice(11).reduce((sum,r)=>sum+Number(r.value),0)}] : source;
    const total=rows.reduce((s,r)=>s+Number(r.value),0);
    if(!total) { ensure(25);text('No breakdown data available',LEFT+5,y+10,9);y+=28;return; }
    const height=Math.max(157,Math.min(235,rows.length*18+20));ensure(height+26);
    text(item.title||'Distribution',LEFT+4,y+12,11,true);y+=24;
    const cx=LEFT+82, cy=y+67, radius=61;
    let angle=-Math.PI/2;
    for(let i=0;i<rows.length;i++){
      const share=Number(rows[i].value)/total, next=angle+share*2*Math.PI;
      const parts=Math.max(2,Math.ceil(share*90));let path=`${cx.toFixed(2)} ${(PAGE_H-cy).toFixed(2)} m `;
      for(let j=0;j<=parts;j++){
        const a=angle+(next-angle)*(j/parts);
        path+=`${(cx+radius*Math.cos(a)).toFixed(2)} ${(PAGE_H-cy-radius*Math.sin(a)).toFixed(2)} ${j===0?'l':'l'} `;
      }
      cmd(`${palette[i%palette.length]} rg ${path}h f`);angle=next;
    }
    circle(cx,cy,36,'1 1 1');text(String(total),cx-13,cy+4,17,true);text('total',cx-11,cy+19,8,false,'0.45 0.5 0.58');
    const labelX=LEFT+170;
    rows.forEach((r,i)=>{
      const py=y+7+i*17;rect(labelX,py,9,9,palette[i%palette.length]);
      const value=Number(r.value),pct=(100*value/total).toFixed(1);
      const label=clean(r.label||'Unknown').slice(0,38);
      text(label,labelX+15,py+8,8);text(`${value}  (${pct}%)`,RIGHT-89,py+8,8,true);
    });
    y+=height-7;
  };
  const bars=(item)=>{
    const rows=(item.data||[]).filter(r=>Number.isFinite(Number(r.value)) && Number(r.value)>=0).slice(0,15);
    if(!rows.length){ensure(26);text('No chart data available',LEFT+5,y+10,9);y+=30;return;}
    ensure(45);text(item.title||'Breakdown',LEFT+4,y+12,11,true);y+=23;
    const max=Math.max(1,...rows.map(r=>Number(r.value)));
    for(let i=0;i<rows.length;i++){
      ensure(32);const r=rows[i],label=clean(r.label||'Unknown').slice(0,55),value=Number(r.value);
      text(label,LEFT+3,y+9,8);text(item.percent?`${value.toFixed(1)}%`:String(value),RIGHT-65,y+9,9,true);
      rect(LEFT+3,y+14,RIGHT-LEFT-6,7,'0.91 0.93 0.96');
      if(value>0)rect(LEFT+3,y+14,(RIGHT-LEFT-6)*value/max,7,palette[i%palette.length]);
      y+=33;
    }
    y+=8;
  };
  const kpis=(item)=>{
    const values=(item.values||[]).filter(v=>v.value!==undefined && v.value!==null);
    if(!values.length)return;
    const width=(RIGHT-LEFT-10)/Math.min(values.length,3);
    for(let i=0;i<values.length;i++){
      if(i%3===0){ensure(75);}
      const x=LEFT+(i%3)*width;
      rect(x,y,Math.max(80,width-10),62,'0.94 0.96 0.99');
      text(clean(values[i].label).slice(0,24),x+9,y+16,8,false,'0.32 0.38 0.49');
      text(clean(values[i].value).slice(0,21),x+9,y+43,18,true,'0.17 0.24 0.54');
      if(i%3===2 || i===values.length-1) y+=73;
    }
    y+=6;
  };
  const line = (top) => cmd(`0.86 0.89 0.94 RG 0.6 w ${LEFT} ${(PAGE_H-top).toFixed(2)} m ${RIGHT} ${(PAGE_H-top).toFixed(2)} l S`);
  const band = () => {
    cmd(`0.17 0.24 0.54 rg 0 ${PAGE_H-62} ${PAGE_W} 62 re f`);
    text('iP CAPITAL  /  MANAGEMENT REPORT', LEFT, 37, 12, true, '1 1 1');
    text(`Generated ${generatedAt.toISOString().slice(0,16).replace('T',' ')} UTC`, LEFT, PAGE_H-27, 8, false, '0.43 0.48 0.57');
  };
  // Pages are numbered at the end ("Page 3 of 12"), when the total is known.
  const openPage = () => {
    if(cmds.length) pages.push(cmds.join('\n'));
    cmds=[];pageIndex++; y=85;
    band();
  };
  const ensure = (height=24) => { if(y+height>PAGE_H-57) openPage(); };
  const renderItems = (items) => {
    for (const item of items || []) {
      if (item.kpis) { kpis({values:item.kpis}); continue; }
      if (item.chart === 'donut') { donut(item); continue; }
      if (item.chart === 'bar') { bars(item); continue; }
      if (item.heading) { ensure(25); text(item.heading,LEFT,y,11,true); y+=19; }
      if (item.columns && item.rows) {
        const n=item.columns.length;const colW=(RIGHT-LEFT)/n;
        ensure(40);
        cmd(`0.93 0.95 0.98 rg ${LEFT} ${(PAGE_H-y-5).toFixed(2)} ${RIGHT-LEFT} 23 re f`);
        item.columns.forEach((c,i)=>text(clean(c).slice(0,Math.floor(colW/5.8)),LEFT+i*colW+5,y+10,8,true));y+=31;
        for (const row of item.rows) {
          const wrapped=row.map((v)=>wrap(v,Math.max(10,Math.floor(colW/5.6)-1)).slice(0,5));
          const rowHeight=Math.max(...wrapped.map(v=>v.length))*12+10;
          ensure(rowHeight+3);
          for(let i=0;i<n;i++) wrapped[i].forEach((l,j)=>text(l,LEFT+i*colW+5,y+10+j*12,8));
          y+=rowHeight;line(y);y+=3;
        }
        y+=9;
      }
      if (item.paragraph) for (const l of wrap(item.paragraph,99)) {ensure(16);text(l,LEFT,y,9);y+=14;}
    }
  };
  const heading = (label, notes) => {
    line(y);y+=23;
    text(label,LEFT,y,15,true);y+=25;
    for (const t of notes || []) {
      for (const l of wrap(t,100)) { ensure(15);text(l,LEFT,y,9,false,'0.42 0.46 0.52');y+=14; }
    }
    y+=9;
  };

  // 1. The body: summary sections flow one after another; each annex entry starts on its own page. Where each one starts is recorded
  //    for the contents page.
  const toc = [];
  openPage();
  sections.forEach((section,index) => {
    ensure(80);
    toc.push({ label:`${index+1}. ${section.title}`, page:pageIndex });
    heading(`${index+1}. ${section.title}`, section.notes);
    renderItems(section.items);
    y+=17;
  });
  annex.forEach((entry,i) => {
    if(i>0 || y>85) openPage();
    toc.push({ label:entry.title, page:pageIndex });
    heading(entry.title, entry.notes);
    renderItems(entry.items);
  });
  if(!sections.length && !annex.length) text('No data was available for this report.',LEFT,y,10);
  if(cmds.length) pages.push(cmds.join('\n'));
  const body=pages.splice(0);

  // 2. The cover, then the contents (which can run to more than one page), placed in front of the body.
  const tocPageCount=Math.max(1,Math.ceil(toc.length/TOC_ROWS_PER_PAGE));
  const pageOffset=1+tocPageCount;   // the cover and the contents pages come before body page 1
  cmds=[];
  cmd(`0.17 0.24 0.54 rg 0 ${PAGE_H-300} ${PAGE_W} 300 re f`);
  text('iP CAPITAL',LEFT,92,16,true,'1 1 1');text('MANAGEMENT REPORT',LEFT,112,10,false,'0.80 0.86 1');
  let top=190;for (const l of wrap(title || 'Dashboard report',30).slice(0,4)) { text(l,LEFT,top,28,true,'1 1 1'); top+=36; }
  y=345;
  for (const l of wrap(subtitle || '',90)) { text(l,LEFT,y,11,false,'0.40 0.45 0.55');y+=17; }
  if(period?.from && period?.to){ text(`Ticket period: ${period.from} to ${period.to}`,LEFT,y,11,false,'0.40 0.45 0.55');y+=17; }
  text(`Generated ${generatedAt.toISOString().slice(0,16).replace('T',' ')} UTC`,LEFT,y,11,false,'0.40 0.45 0.55');y+=34;
  text(`Contents begin on page 2. ${sections.length} section(s)${annex.length?`, ${annex.length} annex page group(s)`:''}.`,LEFT,y,10,false,'0.55 0.60 0.68');
  const cover=cmds.join('\n');
  const tocPages=[];
  for(let p=0;p<tocPageCount;p++){
    cmds=[];band();y=85;
    text(p===0?'Contents':'Contents (continued)',LEFT,y+4,19,true);y+=42;
    for(const entry of toc.slice(p*TOC_ROWS_PER_PAGE,(p+1)*TOC_ROWS_PER_PAGE)){
      const number=String(entry.page+pageOffset);
      text(clean(entry.label).slice(0,78),LEFT+4,y,10);
      text(number,RIGHT-8-number.length*5.6,y,10,true);
      y+=8;line(y);y+=15;
    }
    tocPages.push(cmds.join('\n'));
  }

  // 3. Page numbers, now that the total is known (the cover has none).
  const all=[cover,...tocPages,...body];
  const numbered=all.map((page,i)=>i===0?page:`${page}\n${textCmd(`Page ${i+1} of ${all.length}`,RIGHT-56,PAGE_H-27,8,false,'0.43 0.48 0.57')}`);
  const objects=[null];const add=(body)=>{objects.push(body);return objects.length-1;};
  const font=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
  const boldFont=add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
  const pagesRef=add(''); const pageRefs=[];
  for (const page of numbered) {
    const content=add(`<< /Length ${bytes(page).length} >>\nstream\n${page}\nendstream`);
    const ref=add(`<< /Type /Page /Parent ${pagesRef} 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 ${font} 0 R /F2 ${boldFont} 0 R >> >> /Contents ${content} 0 R >>`);
    pageRefs.push(ref);
  }
  objects[pagesRef]=`<< /Type /Pages /Kids [${pageRefs.map(n=>`${n} 0 R`).join(' ')}] /Count ${pageRefs.length} >>`;
  const catalog=add(`<< /Type /Catalog /Pages ${pagesRef} 0 R >>`);
  const fragments=['%PDF-1.4\n%\xff\xff\xff\xff\n'];const offsets=[0];let offset=bytes(fragments[0]).length;
  for(let i=1;i<objects.length;i++) { offsets.push(offset);const chunk=`${i} 0 obj\n${objects[i]}\nendobj\n`;fragments.push(chunk);offset+=bytes(chunk).length; }
  const xref=offset;let trailer=`xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for(let i=1;i<objects.length;i++) trailer+=`${String(offsets[i]).padStart(10,'0')} 00000 n \n`;
  trailer+=`trailer\n<< /Size ${objects.length} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  fragments.push(trailer);
  return new Blob(fragments.map(bytes),{type:'application/pdf'});
}
export function saveFile(blob, name) {
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
}
